from logging import getLogger

import mindspore as ms
from mindspore import nn,ops

import numpy as np
from mindspore.common.initializer import XavierUniform, initializer
from scipy.sparse.linalg import eigs
from model import loss
from model.abstract_traffic_state_model import AbstractTrafficStateModel


def scaled_laplacian(w):
    w = w.astype(float)
    n = np.shape(w)[0]
    d = []
    # simple graph, W_{i,i} = 0
    lap = -w
    # get degree matrix d and Laplacian matrix L
    for i in range(n):
        d.append(np.sum(w[i, :]))
        lap[i, i] = d[i]
    # symmetric normalized Laplacian L
    for i in range(n):
        for j in range(n):
            if (d[i] > 0) and (d[j] > 0):
                lap[i, j] = lap[i, j] / np.sqrt(d[i] * d[j])
    lambda_max = eigs(lap, k=1, which='LR')[0][0].real
    # lambda_max \approx 2.0
    # we can replace this sentence by setting lambda_max = 2
    return 2 * lap / lambda_max - np.identity(n)


def cheb_poly(lap, ks):
    n = lap.shape[0]
    lap_list = [np.eye(n), lap[:]]
    for i in range(2, ks):
        lap_list.append(np.matmul(2 * lap, lap_list[-1]) - lap_list[-2])
    # lap_list: (Ks, n*n), Lk (n, Ks*n)
    return np.concatenate(lap_list, axis=-1)


class Align(nn.Cell):
    """
    # align channel_in and channel_out
    """

    def __init__(self, channel_in, channel_out):
        super(Align, self).__init__()
        self.channel_in = channel_in
        self.channel_out = channel_out

    def construct(self, x):
        dim1, dim2, dim3, dim4 = x.shape
        if self.channel_in==self.channel_out:
            return x
        y = ops.Zeros()((dim1, self.channel_out - self.channel_in, dim3, dim4), x.dtype)
        x_align = ops.Concat(axis=1)((x, y))
        return x_align


class ConvST(nn.Cell):
    def __init__(self, supports, kt, ks, dim_in, dim_out):
        super(ConvST, self).__init__()
        self.supports = supports.astype(ms.float32)
        self.kt = kt
        self.ks = ks
        self.dim_in = dim_in
        self.dim_out = dim_out
        self.align = Align(channel_in=dim_in, channel_out=dim_out)
        self.weights = ms.Parameter(ms.numpy.rand(
            [2 * self.dim_out, self.ks * self.kt * self.dim_in]))
        self.biases = ms.Parameter(ms.numpy.zeros(2 * self.dim_out))
        self._init_parameters()
        self.sigmoid=ops.Sigmoid()

    def _init_parameters(self):
        for para in self.trainable_params():
            if para.name.find('.bias') != -1:
                para.set_data(ms.numpy.zeros(para.shape))
            elif para.name.find('.weight') != -1:
                para.set_data(initializer(XavierUniform(), para.shape))

    def construct(self, x):
       
        batch_size, len_time, num_nodes = x.shape[0], x.shape[2], x.shape[3]
        assert x.shape[1] == self.dim_in
        res_input = self.align(x)  # (B, dim_out, T, num_nodes)
        padding = ms.numpy.zeros([batch_size, self.dim_in, self.kt - 1, num_nodes])
        # extract spatial-temporal relationships at the same time
        x = ops.concat((x, padding), axis=2)
        # inputs.shape = [B, dim_in, len_time+kt-1, N]
        x = ops.stack([x[:, :, i:i + self.kt, :] for i in range(0, len_time)], axis=2)
        # inputs.shape = [B, dim_in, len_time, kt, N]
        x = x.reshape(-1, num_nodes, self.kt * self.dim_in)
        # inputs.shape = [B*len_time, N, kt*dim_in]
        conv_out = self.graph_conv(x, self.supports, self.kt * self.dim_in, 2 * self.dim_out)
        # conv_out: [B*len_time, N, 2*dim_out]
        conv_out = conv_out.reshape(-1, 2 * self.dim_out, len_time, num_nodes)
        # conv_out: [B, 2*dim_out, len_time, N]

        out = (conv_out[:, :self.dim_out, :, :] + res_input) * self.sigmoid(conv_out[:, self.dim_out:, :, :])
        return out  # [B, dim_out, len_time, N]

    def graph_conv(self, inputs, supports, dim_in, dim_out):
        """
        Args:
            inputs: a tensor of shape [batch, num_nodes, dim_in]
            supports: [num_nodes, num_nodes*ks], calculate the chebyshev polynomials in advance to save time
            dim_in:
            dim_out:
        Returns:
            tensor: shape = [batch, num_nodes, dim_out]
        """
        num_nodes = inputs.shape[1]
        assert num_nodes == supports.shape[0]
        assert dim_in == inputs.shape[2]
        # [batch, num_nodes, dim_in] -> [batch, dim_in, num_nodes] -> [batch * dim_in, num_nodes]
        inputs=inputs.transpose(0,2,1)
        x_new = inputs.reshape(-1, num_nodes)
        # [batch * dim_in, num_nodes] * [num_nodes, num_nodes*ks]
        #       -> [batch * dim_in, num_nodes*ks] -> [batch, dim_in, ks, num_nodes]
        x_new = ops.matmul(x_new, supports)
        x_new = x_new.reshape(-1, dim_in, self.ks, num_nodes)
        # [batch, dim_in, ks, num_nodes] -> [batch, num_nodes, dim_in, ks]
        x_new = x_new.transpose(0, 3, 1, 2)
        # [batch, num_nodes, dim_in, ks] -> [batch*num_nodes, dim_in*ks]
        x_new = x_new.reshape(-1, self.ks * dim_in)
        outputs = ops.tensor_dot(x_new,self.weights,((1,),(1,)))
        outputs = ops.add(outputs,self.biases)  # [batch*num_nodes, dim_out]
        outputs = outputs.reshape(-1, num_nodes, dim_out)  # [batch, num_nodes, dim_out]
        return outputs


class AttentionT(nn.Cell):
    def __init__(self,  len_time, num_nodes, d_out, ext_dim):
        super(AttentionT, self).__init__()
        self.len_time = len_time
        self.num_nodes = num_nodes
        self.d_out = d_out
        self.ext_dim = ext_dim
        self.weight1 = ms.Parameter(initializer('normal', (self.len_time, self.num_nodes * self.d_out, 1)))
        self.weight2 = ms.Parameter(initializer('normal', (self.ext_dim, self.len_time)))
        self.bias = ms.Parameter(initializer('Uniform', (self.len_time)))
        self.softmax=ops.Softmax(axis=1)

    def construct(self, query, x):
        # query  # [B, ext_dim]
        # temporal attention: x.shape = [B, d_out, T, N]
        x_in = x.reshape(-1, self.num_nodes * self.d_out, self.len_time)
        # x_in.shape = [B, N*d_out, T]
        x = x_in.transpose(2, 0, 1)
        # x.shape = [T, B, N*d_out]
        score=ops.matmul(x, self.weight1)
        score = score.reshape(-1, self.len_time) + self.bias
        score = score + ops.matmul(query, self.weight2)
        score = self.softmax(ops.tanh(score))
        # score.shape = [B, T]
        x = ops.matmul(x_in, ops.expand_dims(score, axis=-1))
        # x.shape = [B, N*d_out, 1]
        x = x.transpose(0, 2, 1).reshape((-1, 1, self.num_nodes, self.d_out)).transpose(0, 3, 1, 2)
        # x.shape = [B, d_out, 1, N]
        return x


class AttentionC(nn.Cell):
    def __init__(self, num_nodes, d_out, ext_dim):
        super(AttentionC, self).__init__()
        self.num_nodes = num_nodes
        self.d_out = d_out
        self.ext_dim = ext_dim
        self.weight1 = ms.Parameter(initializer('normal',(self.d_out, self.num_nodes, 1)))
        self.weight2 = ms.Parameter(initializer('normal',(self.ext_dim, self.d_out)))
        self.bias = ms.Parameter(ms.numpy.zeros(self.d_out))
        self.softmax=ops.Softmax(axis=1)


    def construct(self, query, x):
        # query  # [B, ext_dim]
        # channel attention: x.shape = [B, d_out, 1, N]
        x_in = x.reshape (-1, self.num_nodes, self.d_out)
        # x_in.shape = [B, N, d_out]
        x = x_in.transpose(2, 0, 1)
        # x.shape = [d_out, B, N]
        score = ops.matmul(x, self.weight1)
        score = score.reshape(-1, self.d_out) + self.bias
        score = score + ops.matmul(query, self.weight2)
        score = self.softmax(ops.tanh(score))
        # score.shape = [B, d_out]
        x = ops.matmul(x_in, ops.expand_dims(score, axis=-1)).transpose(0, 2, 1)
        # x.shape = [B, 1, N] (1->dim)
        x = ops.expand_dims(x, axis=2)  # [B, 1(dim), 1(T), N]
        return x


class STG2Seq(AbstractTrafficStateModel):
    def __init__(self, config, data_feature):
        super().__init__(config, data_feature)
        self.adj_mx = self.data_feature.get('adj_mx')
        self.num_nodes = self.data_feature.get('num_nodes', 1)
        self.feature_dim = self.data_feature.get('feature_dim', 2)
        self.output_dim = self.data_feature.get('output_dim', 2)
        self.ext_dim = self.data_feature.get('ext_dim', 1)
        # 适用于grid的代码备份
        # self.len_row = self.data_feature.get('len_row', 32)
        # self.len_column = self.data_feature.get('len_column', 32)
        self._scaler = self.data_feature.get('scaler')
        self._logger = getLogger()

        self.input_window = config.get('input_window', 1)
        self.output_window = config.get('output_window', 1)
        self.window = config.get('window', 3)
        self.dim_out = config.get('dim_out', 32)
        self.ks = config.get('ks', 3)
        self.supports = ms.Tensor(cheb_poly(scaled_laplacian(self.adj_mx), self.ks))

        self.long_term_layer = nn.SequentialCell(
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.output_dim, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=2, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
        )

        self.short_term_gcn = nn.SequentialCell(
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.output_dim, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
            ConvST(self.supports, kt=3, ks=self.ks, dim_in=self.dim_out, dim_out=self.dim_out),
            nn.BatchNorm2d(self.dim_out),
        )

        self.attention_t = AttentionT(self.input_window + self.window,
                                      self.num_nodes, self.dim_out, self.ext_dim)
        self.attention_c_1 = AttentionC(self.num_nodes, self.dim_out, self.ext_dim)
        self.attention_c_2 = AttentionC(self.num_nodes, self.dim_out, self.ext_dim)

        self.loss_fn=nn.MSELoss()
        self.mode="train"

    def train(self):
        self.mode = "train"

    def eval(self):
        self.mode = "eval"

    def set_loss(self, loss_fn):
        pass

    def forward(self, X,y):

        inputs = X[:, :, :, :self.output_dim] # (B, input_window, N, output_dim)
        inputs = inputs.transpose(0, 3, 1, 2)  # (B, output_dim, input_window, N)
        # input_ext = batch['X'][:, :, 0, self.output_dim:].contiguous()  # (B, input_window, ext_dim)
        batch_size, input_dim, len_time, num_nodes = inputs.shape
        assert num_nodes == self.num_nodes
        assert len_time == self.input_window
        assert input_dim == self.output_dim

        labels =y[:, :, :, :self.output_dim]  # (B, output_window, N, output_dim)
        labels = labels.transpose(0, 3, 1, 2)  # (B, output_dim, output_window, N)
        labels_ext = y[:, :, 0, self.output_dim:]  # (B, output_window, ext_dim)

        long_output = self.long_term_layer(inputs)  # (B, dim_out, input_window, N)
        preds = []

        if self.mode=="train":
            label_padding = inputs[:, :, -self.window:, :]  # (B, feature_dim, window, N)
            padded_labels = ops.concat((label_padding, labels), axis=2)  # (B, feature_dim, window+output_window, N)
            padded_labels = ops.stack([padded_labels[:, :, i:i + self.window, :]
                                         for i in range(0, self.output_window)], axis=2)
            # (B, feature_dim, output_window, window, N)
            for i in range(0, self.output_window):
                s_inputs = padded_labels[:, :, i, :, :]  # (B, feature_dim, window, N)
                ext_input = labels_ext[:, i, :]  # (B, ext_dim)
                short_output = self.short_term_gcn(s_inputs)  # (B, dim_out, window, N)
                ls_inputs = ops.concat((short_output, long_output), axis=2)
                # (B, dim_out, input_window + window, N)
                ls_inputs = self.attention_t(ext_input, ls_inputs)
                if self.output_dim == 1:
                    pred = self.attention_c_1(ext_input, ls_inputs)
                elif self.output_dim == 2:
                    pred = ops.concat((self.attention_c_1(ext_input, ls_inputs),
                                      self.attention_c_2(ext_input, ls_inputs)), axis=1)
                else:
                    raise ValueError('Error Set output_dim!')
                # pred: (B, output_dim, 1, N)
                label_padding = ops.concat((label_padding[:, :, 1:, :], pred), axis=2)
                preds.append(pred)
        else:
            label_padding = inputs[:, :, -self.window:, :]  # (B, feature_dim, window, N)
            for i in range(0, self.output_window):
                s_inputs = label_padding
                ext_input = labels_ext[:, i, :]  # (B, ext_dim)
                short_output = self.short_term_gcn(s_inputs)  # (B, dim_out, window, N)
                ls_inputs = ops.concat((short_output, long_output), axis=2)
                # (B, dim_out, input_window + window, N)
                ls_inputs = self.attention_t(ext_input, ls_inputs)
                if self.output_dim == 1:
                    pred = self.attention_c_1(ext_input, ls_inputs)
                elif self.output_dim == 2:
                    pred = ops.concat((self.attention_c_1(ext_input, ls_inputs),
                                      self.attention_c_2(ext_input, ls_inputs)), axis=1)
                else:
                    raise ValueError('Error Set output_dim!')
                # pred: (B, output_dim, 1, N)
                label_padding = ops.concat((label_padding[:, :, 1:, :], pred), axis=2)
                preds.append(pred)
        return ops.concat(preds, axis=2).transpose(0, 2, 3, 1)

    def calculate_loss(self, X, y):
        y_predicted = self.predict(X,y)
        y_true = self._scaler.inverse_transform(y[..., :self.output_dim])
        y_predicted = self._scaler.inverse_transform(y_predicted[..., :self.output_dim])
        return loss.masked_mse_m(y_predicted, y_true)

    def predict(self, X,y):
        return self.forward(X,y)

    def construct(self, X,y):
        X=X.astype(ms.float32)
        y=y.astype(ms.float32)
        if self.mode == "train":
            return self.calculate_loss(X, y)
        elif self.mode == "eval":
            y_preds = self.predict(X, y)
            y = self._scaler.inverse_transform(y[..., :self.output_dim])
            y_preds = self._scaler.inverse_transform(y_preds[..., :self.output_dim])
            return y_preds, y

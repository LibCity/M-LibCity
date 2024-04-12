# M-LibCity-npu
## 特殊注意事项
*   当前文件目录仅支持NPU后端的M-LibCity，请确保执行环境下有可用的NPU设备。
*   基于NPU进行多卡训练或推理之前，需要先运行hccl.py生成运行所需的配置文件。具体操作为：```python hccl_tool.py [x]```，其中[x]指具体要使用的卡数。

## 数据集
所有数据集都应存放在raw_data下。
缺少的数据集可以从网站 https://pan.baidu.com/s/1qEfcXBO-QwZfiT0G3IYMpQ with psw 1231 or https://drive.google.com/drive/folders/1g5v2Gq1tkOq8XO0HDCZ9nOTtRpB6-gPe?usp=sharing 中获得。
如想自行处理数据集，可以参照 https://github.com/LibCity/Bigscity-LibCity-Datasets 中的处理脚本。

## 快速运行代码命令
### 单卡训练
```
python run_model.py [task] [model_name] [dataset]
```

### 多卡训练
启动方式为：
```
bash run_with_multi_devices.sh 2 [task] [model_name] [dataset]
```
PS: 参数`2`表示卡数为2。


# M-LibCity-gpu
## 特殊注意事项
*   当前文件目录为支持GPU后端的M-LibCity，请确保执行环境下有可用的GPU设备。
*   GPU端的多卡依赖于mpirun，请确保mpirun命令可用。

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






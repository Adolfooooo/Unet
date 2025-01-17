from torchvision.models import DenseNet
from torchvision.models.densenet import _Transition, _load_state_dict
import torch
import torch.nn.functional as F
from torch import nn
from collections import OrderedDict

'''
dense block 里面的每层会进行两次卷积操作  第一次卷积核的个数为k*4，第二次是k/4，使得每层的输入通道数保持为K
'''


class _DenseUNetEncoder(DenseNet):
    def __init__(self, skip_connections, growth_rate=32, block_config=(6, 12, 24, 16), num_init_features=64, bn_size=4, drop_rate=0):
   # block_config=(6, 12, 24, 16)  为 dense_blocks_num_layers
        super(_DenseUNetEncoder, self).__init__(growth_rate, block_config, num_init_features, bn_size, drop_rate)
        
        self.skip_connections = skip_connections
        
        # remove norm5, classifier
        self.features = nn.Sequential(OrderedDict(list(self.features.named_children())[:-1]))
        delattr(self, 'classifier')
        
        for module in self.features.modules():
            if isinstance(module, nn.AvgPool2d):
                module.register_forward_hook(lambda _, input, output : self.skip_connections.append(input[0]))

    def forward(self, x):
        return self.features(x)
        
class _DenseUNetDecoder(DenseNet):
    def __init__(self, skip_connections, growth_rate=32, block_config=(6, 12, 24, 16), num_init_features=64, bn_size=4, drop_rate=0):
        super(_DenseUNetDecoder, self).__init__(growth_rate, block_config, num_init_features, bn_size, drop_rate)
        
        self.skip_connections = skip_connections
        
        # remove conv0, norm0, relu0, pool0, denseblock4, norm5, classifier
        features = list(self.features.named_children())[4:-2]
        delattr(self, 'classifier')
        
        for i in range(len(features)):
            name, module = features[i]
            if isinstance(module, _Transition):
                m = 2 if i < len(features) - 1 else 1
                features[i] = (name, _TransitionUp(module.conv.in_channels*m, module.conv.out_channels//2, self.skip_connections))

        features.reverse()
        
        self.features = nn.Sequential(OrderedDict(features))
        
        self.final_upsample = nn.Sequential()
        self.final_upsample.add_module('conv0', nn.Conv2d(num_init_features*4, num_init_features, kernel_size=1, stride=1, bias=False))
        self.final_upsample.add_module('norm0', nn.BatchNorm2d(num_init_features))
        self.final_upsample.add_module('relu0', nn.ReLU(inplace=True))

    def forward(self, x, size):
        x = self.features(x)
        x = F.interpolate(x, size, mode='bilinear')
        return self.final_upsample(x)
          
        
class _TransitionUp(nn.Module):
    def __init__(self, num_input_features, num_output_features, skip_connections):
        super(_TransitionUp, self).__init__()
        
        self.skip_connections = skip_connections
        self.block1 = nn.Sequential()
        
        self.block1.add_module('conv1', nn.Conv2d(num_input_features, num_output_features * 2,
                                              kernel_size=1, stride=1, bias=False))
        self.block1.add_module('norm1', nn.BatchNorm2d(num_output_features * 2))
        self.block1.add_module('relu1', nn.ReLU(inplace=True))
        
        self.block2 = nn.Sequential()
        self.block2.add_module('conv2', nn.Conv2d(num_output_features * 4, num_output_features,
                                          kernel_size=1, stride=1, bias=False))
        
    def forward(self, x):
        x = F.interpolate(x, list(self.skip_connections[-1].shape[2:]), mode='bilinear')
        x = self.block1(x)
        x = torch.cat([x, self.skip_connections.pop()], 1)
        x = self.block2(x)
        return x


class DenseUNet(nn.Module):
    def __init__(self, n_classes, pretrained_encoder_url=None, progress=None,skip_connections = None, growth_rate=32,
                 block_config=(6, 12, 24, 16), num_init_features=64, bn_size=4, drop_rate=0, num_input_features = 272,
                 num_output_features = 64):
        super(DenseUNet, self).__init__()
        self.skip_connections = []
        self.encoder = _DenseUNetEncoder(self.skip_connections, growth_rate, block_config, num_init_features, bn_size, drop_rate)
        self.decoder = _DenseUNetDecoder(self.skip_connections, growth_rate, block_config, num_init_features, bn_size, drop_rate)
        self.classifier = nn.Conv2d(64, n_classes, kernel_size=1, stride=1, bias=True)
        # self.softmax = nn.Softmax(dim=1)

        self.encoder._load_state_dict = self.encoder.load_state_dict
        self.encoder.load_state_dict = lambda state_dict : self.encoder._load_state_dict(state_dict, strict=False)
        if pretrained_encoder_url:
            _load_state_dict(self.encoder, pretrained_encoder_url, progress)
        self.encoder.load_state_dict = lambda state_dict : self.encoder._load_state_dict(state_dict, strict=True)

    def forward(self, x):
        size = list(x.shape[2:])
        x = self.encoder(x)
        x = self.decoder(x, size)
        y = self.classifier(x)
        # return self.softmax(y)
        return nn.Sigmoid()(y)




# 使用说明
# from dense_unet import DenseUNet
#
# pretrained_encoder_url = 'https://download.pytorch.org/models/densenet121-a639ec97.pth'
# #
# # for a local file use
# #
# # from pathlib import Path
# # pretrained_encoder_url = Path('/path/to/local/model.pth').resolve().as_url()
# #
#
# n_classes = 3
# model = DenseUNet(n_classes, pretrained_encoder_url)

# if __name__ == '__main__':
#     d = _TransitionUp(num_input_features = 64, num_output_features = 32, skip_connections = None)
#     print(d)
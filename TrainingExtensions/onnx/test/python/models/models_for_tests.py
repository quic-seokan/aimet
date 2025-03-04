# /usr/bin/env python3.5
# -*- mode: python -*-
# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2019, Qualcomm Innovation Center, Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
#  3. Neither the name of the copyright holder nor the names of its contributors
#     may be used to endorse or promote products derived from this software
#     without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#
#  SPDX-License-Identifier: BSD-3-Clause
#
#  @@-COPYRIGHT-END-@@
# =============================================================================
""" Models for use in unit testing """

# pylint: skip-file
from collections import namedtuple
from typing import Dict, List

import torch.nn.functional as F
from torch import nn as nn
from torchvision.ops import roi_align
import numpy as np
import torch
from onnx import helper, numpy_helper, OperatorSetIdProto, TensorProto, load_model
from onnxruntime.quantization.onnx_quantizer import ONNXModel
from torch.nn.modules.batchnorm import _BatchNorm

from .mobilenet import MockMobileNetV1


import aimet_torch.elementwise_ops as aimet_elementwise


# pylint: disable=too-many-instance-attributes
from aimet_torch.elementwise_ops import Multiply


class SingleResidual(nn.Module):
    """ A model with a single residual connection.
        Use this model for unit testing purposes. """

    def __init__(self, num_classes=10):
        super(SingleResidual, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        # All layers above are same as ResNet
        # The output of the MaxPool2d is used as a residual.

        # The following layers are considered as single block.
        self.conv2 = nn.Conv2d(32, 16, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn2 = nn.BatchNorm2d(16)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv3 = nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2, bias=False)

        # The output of Conv2d layer above(conv3) is added with the the residual from
        # MaxPool2d and then fed to the relu layer below.
        self.relu3 = nn.ReLU(inplace=True)

        self.avgpool = nn.AvgPool2d(3, stride=1)
        self.conv4 = nn.Conv2d(32, 8, kernel_size=2, stride=2, padding=2, bias=True)
        self.ada = nn.AdaptiveAvgPool2d(5)
        self.fc = nn.Linear(72, num_classes)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)

        # Save the output of MaxPool as residual.
        residual = x

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)
        x = self.conv3(x)

        # Add the residual
        # AdaptiveAvgPool2d is used to get the desired dimension before adding.
        residual = self.conv4(residual)
        residual = self.ada(residual)
        x += residual
        x = self.relu3(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class SingleResidualWithAvgPool(nn.Module):
    """ A model with a single residual connection.
        Use this model for unit testing purposes. """

    def __init__(self, num_classes=10):
        super(SingleResidualWithAvgPool, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        # All layers above are same as ResNet
        # The output of the MaxPool2d is used as a residual.

        # The following layers are considered as single block.
        self.conv2 = nn.Conv2d(32, 16, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn2 = nn.BatchNorm2d(16)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv3 = nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2, bias=False)

        # The output of Conv2d layer above(conv3) is added with the the residual from
        # MaxPool2d and then fed to the relu layer below.
        self.relu3 = nn.ReLU(inplace=True)

        self.avgpool = nn.AvgPool2d(3, stride=1)
        self.conv4 = nn.Conv2d(32, 8, kernel_size=2, stride=2, padding=2, bias=True)
        self.ada = nn.AvgPool2d(5)
        self.fc = nn.Linear(72, num_classes)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)

        # Save the output of MaxPool as residual.
        residual = x

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)
        x = self.conv3(x)

        # Add the residual
        # AdaptiveAvgPool2d is used to get the desired dimension before adding.
        residual = self.conv4(residual)
        residual = self.ada(residual)
        x += residual
        x = self.relu3(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class SingleResidualWithModuleAdd(nn.Module):
    """ A model with a single residual connection.
        Use this model for unit testing purposes. """

    def __init__(self, num_classes=10):
        super(SingleResidualWithModuleAdd, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        # All layers above are same as ResNet
        # The output of the MaxPool2d is used as a residual.

        # The following layers are considered as single block.
        self.conv2 = nn.Conv2d(32, 16, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn2 = nn.BatchNorm2d(16)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv3 = nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2, bias=False)

        # The output of Conv2d layer above(conv3) is added with the the residual from
        # MaxPool2d and then fed to the relu layer below.
        self.add = aimet_elementwise.Add()
        self.relu3 = nn.ReLU(inplace=True)

        self.avgpool = nn.AvgPool2d(3, stride=1)
        self.conv4 = nn.Conv2d(32, 8, kernel_size=2, stride=2, padding=2, bias=True)
        self.ada = nn.AdaptiveAvgPool2d(5)
        self.fc = nn.Linear(72, num_classes)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)

        # Save the output of MaxPool as residual.
        residual = x

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)
        x = self.conv3(x)

        # Add the residual
        # AdaptiveAvgPool2d is used to get the desired dimension before adding.
        residual = self.conv4(residual)
        residual = self.ada(residual)
        x = self.add(x, residual)
        x = self.relu3(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class MultiInput(nn.Module):
    """ A model with multiple inputs.
        Use this model for unit testing purposes. """

    def __init__(self, num_classes=3):
        super(MultiInput, self).__init__()
        self.conv1 = nn.Conv2d(3, 16, kernel_size=2, stride=2, padding=3, bias=False)
        self.conv2 = nn.Conv2d(16, 8, kernel_size=3, stride=2, padding=2)
        self.conv3 = nn.Conv2d(3, 8, kernel_size=3, stride=2, padding=2)
        self.bn1 = nn.BatchNorm2d(8)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        self.fc = nn.Linear(288, num_classes)

    def forward(self, *inputs):
        x1 = self.conv1(inputs[0])
        x1 = self.conv2(x1)
        x2 = self.conv3(inputs[1])
        x = x1 + x2
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class DictInputModel(nn.Module):
    """ Model with dictionary as input. """
    def __init__(self, num_classes=3):
        super(DictInputModel, self).__init__()
        self.conv1 = nn.Conv2d(3, 16, kernel_size=2, stride=2, padding=3, bias=False)
        self.conv2 = nn.Conv2d(16, 8, kernel_size=3, stride=2, padding=2)
        self.conv3 = nn.Conv2d(3, 8, kernel_size=3, stride=2, padding=2)
        self.bn1 = nn.BatchNorm2d(8)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        self.fc = nn.Linear(288, num_classes)

    def forward(self, *inputs):
        x1 = self.conv1(inputs[0]['inp_1'])
        x1 = self.conv2(x1)
        x2 = self.conv3(inputs[0]['inp_2'])
        x = x1 + x2
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ConcatModel(nn.Module):
    """ A model with concat op.
        Use this model for unit testing purposes.
        Expected inputs: 3 inputs, all of size (1, 3, 8, 8) """

    def __init__(self, num_classes=3):
        super(ConcatModel, self).__init__()
        self.conv1 = nn.Conv2d(3, 2, kernel_size=2, stride=2, padding=2, bias=False)
        self.conv2 = nn.Conv2d(3, 4, kernel_size=2, stride=2, padding=2)
        self.conv3 = nn.Conv2d(3, 8, kernel_size=2, stride=2, padding=2)
        self.fc = nn.Linear(504, num_classes)

    def forward(self, *inputs):
        c1 = self.conv1(inputs[0])
        c2 = self.conv2(inputs[1])
        c3 = self.conv3(inputs[2])
        cat_inputs = [c1, c2, c3]
        x = torch.cat(cat_inputs, 1)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ModuleListModel(nn.Module):
    """ A model with modules defined using ModuleLists.
        Use this model for unit testing purposes.
        Expected inputs: 3 inputs, all of size (1, 3, 8, 8) """

    def __init__(self, num_classes=3):
        super(ModuleListModel, self).__init__()
        self.mod_list = nn.ModuleList([
            nn.MaxPool2d(kernel_size=2, stride=2, padding=1),       # use 4th
            nn.ReLU(inplace=True),      # use 3rd
            nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2),                # use 5th
            nn.ReLU(),      # dummy unused op
            nn.Conv2d(3, 16, kernel_size=2, stride=2, padding=2, bias=False)        # use 1st
        ])
        self.seq_list = nn.Sequential(
            nn.Conv2d(8, 4, kernel_size=2, stride=2, padding=2),                # use 6th
            nn.ReLU(),      # dummy unused op
            nn.BatchNorm2d(16),     # use 2nd
        )
        self.fc = nn.Linear(64, num_classes)

    def forward(self, *inputs):
        x = self.mod_list[4](inputs[0])
        x = self.seq_list[2](x)
        x = self.mod_list[1](x)
        x = self.mod_list[0](x)
        x = self.mod_list[2](x)
        x = self.seq_list[0](x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class TinyModel(nn.Module):
    """ Use this model for unit testing purposes. Expect input shape (1, 3, 32, 32) """

    def __init__(self):
        super(TinyModel, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        self.conv2 = nn.Conv2d(32, 16, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn2 = nn.BatchNorm2d(16)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv3 = nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.relu3 = nn.ReLU(inplace=True)
        self.avgpool = nn.AvgPool2d(3, stride=1)
        self.conv4 = nn.Conv2d(8, 4, kernel_size=2, stride=2, padding=2, bias=True)
        self.fc = nn.Linear(36, 12)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)
        x = self.conv3(x)
        x = self.relu3(x)
        x = self.avgpool(x)
        x = self.conv4(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x

class QuantSimTinyModel(nn.Module):
    """ Use this model for quantsim_config unit testing purposes. Expect input shape (1, 3, 32, 32) """

    def __init__(self):
        super(QuantSimTinyModel, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2, padding=1)
        self.conv2 = nn.Conv2d(32, 16, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn2 = nn.BatchNorm2d(16)
        self.relu2 = nn.ReLU6(inplace=True)
        self.conv3 = nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.relu3 = nn.ReLU(inplace=True)
        self.avgpool = nn.AvgPool2d(3, stride=1)
        self.conv4 = nn.Conv2d(8, 4, kernel_size=2, stride=2, padding=2, bias=True)
        self.fc = nn.Linear(36, 12)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.maxpool(x)

        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)
        x = self.conv3(x)
        x = self.relu3(x)
        x = self.avgpool(x)
        x = self.conv4(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ModelWithDropouts(nn.Module):
    """ Use this model for unit testing purposes. """

    def __init__(self):
        super(ModelWithDropouts, self).__init__()
        self.conv1 = nn.Conv2d(3, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(8)
        self.relu1 = nn.ReLU(inplace=True)
        self.dropout1 = nn.Dropout(p=.4)
        self.dropout2 = nn.Dropout2d(p=.6)
        self.fc = nn.Linear(2592, 10)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.dropout1(x)
        x = self.dropout2(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ModelWithReusedNodes(nn.Module):
    """ Model that reuses a relu module. Expects input of shape (1, 3, 32, 32) """

    def __init__(self):
        super(ModelWithReusedNodes, self).__init__()
        self.conv1 = nn.Conv2d(3, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(8)
        self.relu1 = nn.ReLU(inplace=True)
        self.relu2 = nn.ReLU(inplace=True)
        self.fc = nn.Linear(2592, 10)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.relu1(x)
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.relu2(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ModelWithFunctionalOps(nn.Module):
    """ Model that uses functional modules instead of nn.Modules. Expects input of shape (1, 3, 32, 32) """

    def __init__(self):
        super(ModelWithFunctionalOps, self).__init__()
        self.conv1 = nn.Conv2d(3, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(8)
        self.relu1 = nn.ReLU(inplace=True)
        self.fc = nn.Linear(2592, 10)

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.relu1(x)
        x = self.bn1(x)
        x = F.relu(x)
        x = self.relu1(x)
        x = x.view(x.size(0), -1)
        x = F.linear(x, torch.randn(10, 2592))
        return x


class SequentialModel(nn.Module):
    """ A model with modules defined using nn.Sequential.
        Use this model for unit testing purposes.
        Expected inputs: 3 inputs, all of size (1, 3, 8, 8) """

    def __init__(self, num_classes=3):
        super(SequentialModel, self).__init__()
        self.seq_list = nn.Sequential(
            nn.Conv2d(3, 16, kernel_size=2, stride=2, padding=2, bias=False),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=1),
            nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2),
            nn.Conv2d(8, 4, kernel_size=2, stride=2, padding=2)
        )
        self.fc = nn.Linear(64, num_classes)

    def forward(self, *inputs):
        x = self.seq_list(inputs[0])
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class BasicConv2d(nn.Module):
    """ A Simple Super Node Model used as building block in Hierarchical Model  """

    def __init__(self, **kwargs):
        super(BasicConv2d, self).__init__()
        self.conv = nn.Conv2d(64, 64, bias=False, **kwargs)
        self.dropout = torch.nn.Dropout(p=0.1)
        self.bn = nn.BatchNorm2d(64, eps=0.001)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, *inputs):
        x = self.conv(inputs[0])
        x = self.dropout(x)
        x = self.bn(x)
        return self.relu(x)


class MultiConv2dModel(nn.Module):
    """ Sequential Model contains sequences of BasicConv2d Model  """

    def __init__(self):
        super(MultiConv2dModel, self).__init__()
        self.seq_list = nn.Sequential(
            BasicConv2d(kernel_size=3),
            BasicConv2d(kernel_size=1),
            BasicConv2d(kernel_size=3)
        )

    def forward(self, *inputs):
        return self.seq_list(inputs[0])


class NestedModel(nn.Module):
    """ Aggregation Model contains two instance of Tiny Model """

    def __init__(self):
        super(NestedModel, self).__init__()
        self.tm1 = TinyModel()
        self.tm2 = TinyModel()

    def forward(self, *inputs):
        c1 = self.tm1(inputs[0])
        c2 = self.tm2(inputs[1])
        cat_inputs = [c1, c2]
        x = torch.cat(cat_inputs, 1)
        return x


class HierarchicalModel(nn.Module):
    """ Aggregation Model contains multi-level of PyTorch Module
        Expected 5 inputs with shapes  in the following order:
            (1, 64, 32, 32)
            (1,  3, 32, 32)
            (1, 64, 32, 32)
            (1,  3, 32, 32)
            (1,  3,  8,  8) """

    def __init__(self):
        super(HierarchicalModel, self).__init__()
        self.conv1 = BasicConv2d(kernel_size=3)
        self.conv2 = BasicConv2d(kernel_size=3)
        self.multi_conv = MultiConv2dModel()
        self.nm1 = NestedModel()
        self.nm2 = NestedModel()
        self.sq = SequentialModel()

    def forward(self, *inputs):
        x = self.conv1((inputs[0]))
        x = x.narrow(1, 0, 3)
        c1 = self.nm1(x, inputs[1])
        x = self.conv2(inputs[2])
        x = self.multi_conv(x)
        x = x.narrow(1, 0, 3)
        c2 = self.nm2(x, inputs[3])
        c3 = self.sq(inputs[4])
        cat_inputs = [c1, c2, c3]
        x = torch.cat(cat_inputs, 1)
        return x


class PassThroughOpLastLayerModel(nn.Module):
    """ Model with PassThroughOp as last layer. Expect input shape (1, 3, 32, 32) """

    def __init__(self):
        super(PassThroughOpLastLayerModel, self).__init__()
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.passthrough = torch.nn.Identity()

    def forward(self, *inputs):
        x = self.conv1(inputs[0])
        x = self.passthrough(x)
        return x


class TransposedConvModel(torch.nn.Module):
    """
    Model with transposed conv2D
    """
    def __init__(self):
        super(TransposedConvModel, self).__init__()
        self.conv1 = torch.nn.ConvTranspose2d(10, 10, 3)
        self.bn1 = torch.nn.BatchNorm2d(10)
        self.relu1 = torch.nn.ReLU()

        self.conv2 = torch.nn.ConvTranspose2d(10, 10, 3)
        self.bn2 = torch.nn.BatchNorm2d(10)

    # pylint: disable=arguments-differ
    def forward(self, x):
        # Regular case - conv followed by bn
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)

        x = self.conv2(x)
        x = self.bn2(x)
        return x


class TransposedConvModelWithoutBN(torch.nn.Module):
    """
    Model with transposed conv2D
    """
    def __init__(self):
        super(TransposedConvModelWithoutBN, self).__init__()
        self.conv1 = torch.nn.ConvTranspose2d(10, 10, 3)
        self.relu1 = torch.nn.ReLU()
        self.conv2 = torch.nn.ConvTranspose2d(10, 10, 3)

    # pylint: disable=arguments-differ
    def forward(self, x):
        # Regular case - conv followed by bn
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.conv2(x)
        return x


class TupleOutputModel(torch.nn.Module):
    """
    Model with Tuple of Tensors as output
    """
    def __init__(self):
        super(TupleOutputModel, self).__init__()
        self.conv1 = torch.nn.Conv2d(3, 2, kernel_size=3, padding=1, bias=False)
        self.conv2 = torch.nn.Conv2d(3, 4, kernel_size=3, padding=1)
        self.conv3 = torch.nn.Conv2d(3, 6, kernel_size=3, padding=1)

    def forward(self, *inputs):
        c1 = self.conv1(inputs[0])
        c2 = self.conv2(inputs[0])
        c3 = self.conv3(inputs[0])
        return c1, c2, c3


class MultiOutputModel(torch.nn.Module):
    """
    Model with Tuple of Tensors as output
    """
    def __init__(self):
        super(MultiOutputModel, self).__init__()
        self.layer = TupleOutputModel()
        self.conv1 = torch.nn.Conv2d(2, 4, kernel_size=3, padding=1)
        self.conv2 = torch.nn.Conv2d(4, 4, kernel_size=3, padding=1)
        self.conv3 = torch.nn.Conv2d(6, 4, kernel_size=3, padding=1)

    def forward(self, *inputs):
        x, y, z = self.layer(inputs[0])
        x1 = self.conv1(x)
        x2 = self.conv2(y)
        x3 = self.conv3(z)
        return torch.cat([x1, x2, x3], 1)


class ConfigurableTupleOutputModel(torch.nn.Module):
    """
    Model with Tuple of Tensors as output with configurable channels
    """
    def __init__(self, channels=(2, 4, 6)):
        super(ConfigurableTupleOutputModel, self).__init__()
        self.conv1 = torch.nn.Conv2d(channels[0], channels[0], kernel_size=3, padding=1)
        self.conv2 = torch.nn.Conv2d(channels[1], channels[1], kernel_size=3, padding=1)
        self.conv3 = torch.nn.Conv2d(channels[2], channels[2], kernel_size=3, padding=1)

    def forward(self, *inputs):
        c1 = self.conv1(inputs[0])
        c2 = self.conv2(inputs[1])
        c3 = self.conv3(inputs[2])
        return c1, c2, c3


class SingleLayerRNNModel(nn.Module):
    """
    Model using torch.nn.RNN module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(SingleLayerRNNModel, self).__init__()
        self.rnn = torch.nn.RNN(input_size=3, hidden_size=5, num_layers=1)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.rnn(x, hx)


class SingleLayerBidirectionalLstmModel(nn.Module):
    """
    Model using torch.nn.LSTM module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(SingleLayerBidirectionalLstmModel, self).__init__()
        self.lstm = torch.nn.LSTM(input_size=3, hidden_size=5, num_layers=1, bidirectional=True)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.lstm(x, hx)


class TwoLayerBidirectionalLSTMModel(nn.Module):
    """
    Model using torch.nn.LSTM module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(TwoLayerBidirectionalLSTMModel, self).__init__()
        self.recurrent = torch.nn.LSTM(input_size=3, hidden_size=5, num_layers=2, bidirectional=True)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.recurrent(x, hx)


class TwoLayerBidirectionaRNNModel(nn.Module):
    """
    Model using torch.nn.RNN module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(TwoLayerBidirectionaRNNModel, self).__init__()
        self.recurrent = torch.nn.RNN(input_size=3, hidden_size=5, num_layers=2, bidirectional=True)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.recurrent(x, hx)


class TwoLayerBidirectionalGRUModel(nn.Module):
    """
    Model using torch.nn.GRU module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(TwoLayerBidirectionalGRUModel, self).__init__()
        self.recurrent = torch.nn.GRU(input_size=3, hidden_size=5, num_layers=2, bidirectional=True)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.recurrent(x, hx)


class MultiLayerRNNModel(nn.Module):
    """
    Model using torch.nn.RNN module with multiple layers
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(MultiLayerRNNModel, self).__init__()
        self.rnn = torch.nn.RNN(input_size=3, hidden_size=5, num_layers=2)

    # pylint: disable=arguments-differ
    def forward(self, x, hx=None):
        return self.rnn(x, hx)


class RNNCellModel(nn.Module):
    """
    Model using torch.nn.RNNCell module
    Expected input shape = (SEQ_LENGTH=10, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(RNNCellModel, self).__init__()
        self.rnn_cell = torch.nn.RNNCell(input_size=3, hidden_size=5)

    # pylint: disable=arguments-differ
    def forward(self, x, hx0=None):
        output = []
        for i in range(x.shape[0]):
            hx0 = self.rnn_cell(x[i], hx0)
            output.append(hx0)
        return tuple(output), hx0


class LSTMModel(nn.Module):
    """
    Model using torch.nn.LSTM module
    Expected input shape = (SEQ_LENGTH, BATCH_SIZE, INPUT_SIZE=3)
    """
    def __init__(self):
        super(LSTMModel, self).__init__()
        self.rnn = torch.nn.LSTM(input_size=3, hidden_size=5, num_layers=1)

    # pylint: disable=arguments-differ
    def forward(self, x, hx_cx=None):
        return self.rnn(x, hx_cx)


class NestedSequentialModel(nn.Module):
    """
    Model using nested Sequential modules
    Expected input shape = (1, 3, 8, 8)
    """
    def __init__(self, num_classes=3):
        super(NestedSequentialModel, self).__init__()
        self.inner_seq = nn.Sequential(
            nn.Conv2d(3, 16, kernel_size=2, stride=2, padding=2, bias=False),
            nn.BatchNorm2d(16)
        )
        self.seq_list = nn.Sequential(
            self.inner_seq,
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=1),
            nn.Conv2d(16, 8, kernel_size=2, stride=2, padding=2),
            nn.Conv2d(8, 4, kernel_size=2, stride=2, padding=2)
        )
        self.fc = nn.Linear(64, num_classes)

    def forward(self, *inputs):
        x = self.seq_list(inputs[0])
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class ModelWithFunctionalReLU(nn.Module):
    """ Model that uses functional ReLU instead of nn.Modules. Expects input of shape (1, 3, 32, 32) """
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool1(F.relu(self.conv1(x)))
        x = self.pool2(F.relu(self.conv2(x)))
        x = torch.flatten(x, 1)
        x = F.relu(self.fc1(x))
        x = self.fc2(x).relu()
        x = self.fc3(x)
        return x


class ModelWithDuplicateReLU(nn.Module):
    """ Model that uses single ReLU instances multiple times in the forward. Expects input of shape (1, 3, 32, 32) """
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.relu = nn.ReLU()
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu(x)
        x = self.pool1(x)
        x = self.conv2(x)
        x = self.relu(x)
        x = self.pool2(x)
        x = torch.flatten(x, 1)
        x = self.fc1(x)
        x = self.relu(x)
        x = self.fc2(x).relu()
        x = self.fc3(x)
        return x


class ModelWithTwoInputs(nn.Module):

    def __init__(self):
        super(ModelWithTwoInputs, self).__init__()
        self.conv1_a = nn.Conv2d(1, 10, kernel_size=5)
        self.maxpool1_a = nn.MaxPool2d(2)
        self.relu1_a = nn.ReLU()

        self.conv1_b = nn.Conv2d(1, 10, kernel_size=5)
        self.maxpool1_b = nn.MaxPool2d(2)
        self.relu1_b = nn.ReLU()

        self.conv2 = nn.Conv2d(10, 20, kernel_size=5)
        self.maxpool2 = nn.MaxPool2d(2)
        self.relu2 = nn.LeakyReLU()
        self.flatten = nn.Flatten()

        self.fc1 = nn.Linear(320, 50)
        self.relu3 = nn.ReLU()
        self.dropout = nn.Dropout()
        self.fc2 = nn.Linear(50, 10)

        self.softmax = nn.LogSoftmax(dim=1)

    def forward(self, x1, x2):
        x1 = self.relu1_a(self.maxpool1_a(self.conv1_a(x1)))
        x2 = self.relu1_b(self.maxpool1_b(self.conv1_b(x2)))
        x = x1 + x2
        x = self.relu2(self.maxpool2(self.conv2(x)))
        x = self.flatten(x)
        x = self.relu3(self.fc1(x))
        x = self.dropout(x)
        x = self.fc2(x)
        return self.softmax(x)


class ModelWithTransposeConv(nn.Module):

    def __init__(self):
        super(ModelWithTransposeConv, self).__init__()
        self.conv1_a = nn.Conv2d(1, 10, kernel_size=5)
        self.maxpool1_a = nn.MaxPool2d(2)
        self.relu1_a = nn.ReLU()

        self.conv1_b = nn.Conv2d(1, 10, kernel_size=5)
        self.maxpool1_b = nn.MaxPool2d(2)
        self.relu1_b = nn.ReLU()

        self.conv2 = nn.ConvTranspose2d(10, 20, kernel_size=5)
        self.maxpool2 = nn.MaxPool2d(2)
        self.relu2 = nn.LeakyReLU()
        self.flatten = nn.Flatten()

        self.fc1 = nn.Linear(1280, 50)
        self.relu3 = nn.ReLU()
        self.dropout = nn.Dropout()
        self.fc2 = nn.Linear(50, 10)

        self.softmax = nn.LogSoftmax(dim=1)

    def forward(self, x1, x2):
        x1 = self.relu1_a(self.maxpool1_a(self.conv1_a(x1)))
        x2 = self.relu1_b(self.maxpool1_b(self.conv1_b(x2)))
        x = x1 + x2
        x = self.relu2(self.maxpool2(self.conv2(x)))
        x = self.flatten(x)
        x = self.relu3(self.fc1(x))
        x = self.dropout(x)
        x = self.fc2(x)
        return self.softmax(x)


class SimpleConditional(torch.nn.Module):
    """
    Model using conditional paths
    Expected input shape = (1, 3)
    """
    def __init__(self):
        super(SimpleConditional, self).__init__()
        self.prelu1 = torch.nn.PReLU(init=.3)
        self.prelu2 = torch.nn.PReLU(init=.4)
        self.linear1 = torch.nn.Linear(3, 2)
        self.linear2 = torch.nn.Linear(3, 10)
        self.softmax = torch.nn.Softmax()

    def forward(self, _input, condition):
        if condition:
            x = self.linear1(_input)
            x = x.view(x.size(0), -1)
            x = self.prelu1(x)
            return x
        x = self.linear2(_input)
        x = self.prelu2(x)
        x = self.softmax(x)
        return x


class LinearAndLSTMModel(torch.nn.Module):
    def __init__(self):
        super(LinearAndLSTMModel, self).__init__()

        self.linear = torch.nn.Linear(10, 4)
        self.prelu = torch.nn.PReLU(init=.3)
        self.recurrent = torch.nn.LSTM(input_size=4, hidden_size=5, num_layers=2)

    def forward(self, x, h_and_c=None):
        x = self.linear(x)
        x = self.prelu(x)
        x = torch.unsqueeze(x, 1)
        return self.recurrent(x, h_and_c)



class RoiAlignPyTorch(torch.nn.Module):

    def __init__(self, aligned_height, aligned_width, spatial_scale):
        super(RoiAlignPyTorch, self).__init__()
        self.aligned_width = int(aligned_width)
        self.aligned_height = int(aligned_height)
        self.spatial_scale = float(spatial_scale)

    def forward(self, features, rois):
        return roi_align(input=features,
                         boxes = rois,
                         output_size = [self.aligned_height, self.aligned_width],
                         spatial_scale = self.spatial_scale,
                         sampling_ratio = 0)

class RoiModel(torch.nn.Module):

    def __init__(self, height, width, scale):
        super(RoiModel, self).__init__()
        self.roi = RoiAlignPyTorch(height, width, scale)

    def forward(self, *inputs):
        return self.roi(*inputs)


class InputOutputDictModel(nn.Module):
    def __init__(self):
        super(InputOutputDictModel, self).__init__()
        self.mul1 = Multiply()
        self.mul2 = Multiply()
        self.mul3 = Multiply()

    def forward(self, inputs: Dict[str, torch.Tensor]):
        ab = self.mul1(inputs['a'], inputs['b'])
        bc = self.mul2(inputs['b'], inputs['c'])
        ca = self.mul3(inputs['c'], inputs['a'])

        output_def = namedtuple('output_def', ['ab', 'bc', 'ca'])
        return output_def(ab, bc, ca)


class Float32AndInt64InputModel(nn.Module):
    """
    This model uses a list of Tensors as input. The input Tensor list contains both float32 and int63 tensors.
    """
    def __init__(self):
        super(Float32AndInt64InputModel, self).__init__()
        self.index_feature_map = 0
        self.index_x = 1
        self.index_y = 2
        self.conv1 = nn.Conv2d(3, 32, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.add = aimet_elementwise.Add()

    def forward(self, *inputs: List[torch.Tensor]):
        grid_x = inputs[self.index_x]
        grid_y = inputs[self.index_y]

        x = inputs[self.index_feature_map]
        x = self.conv1(x)
        x = self.bn1(x)
        f_00 = x[:, :, grid_x, grid_y]
        f_01 = x[:, :, grid_y, grid_x]
        return self.add(f_00, f_01)


# pylint: disable=no-member
def build_dummy_model():
    """BUild dummy ONNX model for testing"""
    op = OperatorSetIdProto()
    op.version = 13
    input_info = helper.make_tensor_value_info(name='input', elem_type=TensorProto.FLOAT,
                                               shape=[1, 3, 32, 32])

    output_info = helper.make_tensor_value_info(name='output', elem_type=TensorProto.FLOAT,
                                                shape=[1, 10])
    conv_node = helper.make_node('Conv',
                                 ['input', 'conv_w', 'conv_b'],
                                 ['3'],
                                 'conv',
                                 kernel_shape=[3, 3],
                                 pads=[1, 1, 1, 1],)
    relu_node = helper.make_node('Relu',
                                 ['3'],
                                 ['4'],
                                 'relu')
    maxpool_node = helper.make_node('MaxPool',
                                    ['4'],
                                    ['5'],
                                    'maxpool',
                                    kernel_shape=[3, 3],
                                    pads=[1, 1, 1, 1],
                                    strides=[2, 2],)

    flatten_node = helper.make_node('Flatten',
                                    ['5'],
                                    ['6'],
                                    'flatten')
    fc_node = helper.make_node('Gemm',
                               ['6', 'fc_w', 'fc_b'],
                               ['output'],
                               'fc')

    conv_w_init = numpy_helper.from_array(np.random.rand(1, 3, 3, 3).astype(np.float32), 'conv_w')
    conv_b_init = numpy_helper.from_array(np.random.rand(1).astype(np.float32), 'conv_b')
    fc_w_init = numpy_helper.from_array(np.random.rand(256, 10).astype(np.float32), 'fc_w')
    fc_b_init = numpy_helper.from_array(np.random.rand(10).astype(np.float32), 'fc_b')

    onnx_graph = helper.make_graph([conv_node, relu_node, maxpool_node, flatten_node, fc_node],
                                   'dummy_graph', [input_info], [output_info],
                                   [conv_w_init, conv_b_init, fc_w_init, fc_b_init])

    model = helper.make_model(onnx_graph, opset_imports=[op])

    return model

def single_residual_model():
    x = torch.randn(1, 3, 32, 32, requires_grad=True)
    model = SingleResidualWithAvgPool()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_single_residual.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_single_residual.onnx'))
    return model

def multi_input_model():
    x = (torch.rand(32, 1, 28, 28, requires_grad=True), torch.rand(32, 1, 28, 28, requires_grad=True))
    model = ModelWithTwoInputs()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_multi_input.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_multi_input.onnx'))
    return model

def transposed_conv_model():
    x = torch.randn(10, 10, 4, 4, requires_grad=True)
    model = TransposedConvModel()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_transposed_conv.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_transposed_conv.onnx'))
    return model

def transposed_conv_model_without_bn():
    x = torch.randn(10, 10, 4, 4, requires_grad=True)
    model = TransposedConvModelWithoutBN()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_transposed_conv_without_bn.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_transposed_conv_without_bn.onnx'))
    return model

def depthwise_conv_model():
    x = torch.randn(1, 3, 224, 224, requires_grad=True)
    model = MockMobileNetV1()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_mock_mobilenet.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_mock_mobilenet.onnx'))
    return model

def concat_model():
    x = (torch.rand(1, 3, 8, 8, requires_grad=True), torch.rand(1, 3, 8, 8, requires_grad=True),
         torch.rand(1, 3, 8, 8, requires_grad=True))
    model = ConcatModel()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./concat_model.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./concat_model.onnx'))
    return model

def hierarchical_model():
    conv_shape = (1, 64, 32, 32)
    inp_shape = (1, 3, 32, 32)
    seq_shape = (1, 3, 8, 8)
    [conv_shape, inp_shape, conv_shape, inp_shape, seq_shape]
    x = (torch.rand(1, 64, 32, 32, requires_grad=True), torch.rand(1, 3, 32, 32, requires_grad=True),
         torch.rand(1, 64, 32, 32, requires_grad=True), torch.rand(1, 3, 32, 32, requires_grad=True),
         torch.rand(1, 3, 8, 8, requires_grad=True))
    model = HierarchicalModel()

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./hierarchical_model.onnx",  # where to save the model (can be a file or file-like object)
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=True,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./hierarchical_model.onnx'))
    return model

class BNBeforeConv(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, bias=False):
        super(BNBeforeConv, self).__init__()
        self.conv1 = torch.nn.Conv2d(10, 20, 3, bias=bias)
        self.relu1 = torch.nn.ReLU()
        self.bn1 = torch.nn.BatchNorm2d(20)
        self.conv2 = torch.nn.Conv2d(20, 20, 3, bias=bias, padding=padding, stride=stride,
                                     dilation=dilation, groups=groups)

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.bn1(x)
        x = self.conv2(x)

        return x


class BNAfterConv(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, bias=False):
        super(BNAfterConv, self).__init__()
        self.conv1 = torch.nn.Conv2d(10, 20, 3, bias=bias)
        self.relu1 = torch.nn.ReLU()
        self.conv2 = torch.nn.Conv2d(20, 20, 3, bias=bias, padding=padding, stride=stride,
                                     dilation=dilation, groups=groups)
        self.bn1 = torch.nn.BatchNorm2d(20)
        self.relu2 = torch.nn.ReLU()

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.conv2(x)
        x = self.bn1(x)
        x = self.relu2(x)

        return x


class BNAfterLinear(torch.nn.Module):
    def __init__(self, bias=False):
        super(BNAfterLinear, self).__init__()
        self.fc1 = torch.nn.Linear(10, 20, bias=bias)
        self.bn1 = torch.nn.BatchNorm1d(20)

    def forward(self, x):
        x = self.fc1(x)
        x = self.bn1(x)

        return x


class BNBeforeLinear(torch.nn.Module):
    def __init__(self, bias=False):
        super(BNBeforeLinear, self).__init__()
        self.fc1 = torch.nn.Linear(10, 20, bias=bias)
        self.relu1 = torch.nn.ReLU()
        self.bn1 = torch.nn.BatchNorm1d(20)
        self.fc2 = torch.nn.Linear(20, 20, bias=bias)

    def forward(self, x):
        x = self.fc1(x)
        x = self.relu1(x)
        x = self.bn1(x)
        x = self.fc2(x)

        return x


class BNBeforeFlattenLinear(torch.nn.Module):
    def __init__(self, bias=False):
        super(BNBeforeFlattenLinear, self).__init__()
        self.conv1 = torch.nn.Conv2d(10, 20, 3, padding=1, stride=2)
        self.relu1 = torch.nn.ReLU()
        self.bn1 = torch.nn.BatchNorm2d(20)
        self.flatten = torch.nn.Flatten()
        self.fc2 = torch.nn.Linear(20 * 12 * 12, 20, bias=bias)

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.bn1(x)
        x = self.flatten(x)
        x = self.fc2(x)

        return x


class BNAfterConvTranspose(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, output_padding=0):
        super(BNAfterConvTranspose, self).__init__()
        self.conv1 = torch.nn.ConvTranspose2d(10, 10, 3, padding=padding, stride=stride, dilation=dilation,
                                              groups=groups, output_padding=output_padding)
        self.bn1 = torch.nn.BatchNorm2d(10)
        self.relu1 = torch.nn.ReLU()

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)

        return x


class BNAfterConvTranspose1d(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, output_padding=0):
        super(BNAfterConvTranspose1d, self).__init__()
        self.conv1 = torch.nn.ConvTranspose1d(10, 10, 3, padding=padding, stride=stride, dilation=dilation,
                                              groups=groups, output_padding=output_padding)
        self.bn1 = torch.nn.BatchNorm1d(10)
        self.relu1 = torch.nn.ReLU()
        self.conv2 = torch.nn.ConvTranspose1d(10, 10, 3, padding=padding, stride=stride, dilation=dilation,
                                              groups=groups, output_padding=output_padding)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.conv2(x)
        return x


class BNBeforeConvTranspose(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, output_padding=0):
        super(BNBeforeConvTranspose, self).__init__()
        self.conv1 = torch.nn.Conv2d(10, 10, 3, bias=False)
        self.relu1 = torch.nn.ReLU()
        self.bn1 = torch.nn.BatchNorm2d(10)
        self.conv2 = torch.nn.ConvTranspose2d(10, 10, 3, padding=padding, stride=stride, dilation=dilation,
                                              groups=groups, output_padding=output_padding)

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu1(x)
        x = self.bn1(x)
        x = self.conv2(x)

        return x


class BNAfterConv1d(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, bias=False):
        super(BNAfterConv1d, self).__init__()
        self.conv1 = torch.nn.Conv1d(10, 10, 3, padding=padding, stride=stride, dilation=dilation, groups=groups, bias=bias)
        self.bn1 = torch.nn.BatchNorm1d(10)
        self.relu1 = torch.nn.ReLU()
        self.conv2 = torch.nn.Conv1d(10, 10, 3, padding=padding, stride=stride, dilation=dilation, groups=groups,
                                     bias=bias)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)
        x = self.conv2(x)

        return x

class BNBeforeConv1d(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, bias=False):

        super(BNBeforeConv1d, self).__init__()
        self.bn1 = torch.nn.BatchNorm1d(10)
        self.conv1d = torch.nn.Conv1d(10, 20, 3, padding=padding, stride=stride, dilation=dilation, groups=groups, bias=bias)

    def forward(self, x):
        x = self.bn1(x)
        x = self.conv1d(x)

        return x

class BNAfterDynamicMatMul(torch.nn.Module):
    def __init__(self, padding=0, stride=1, dilation=1, groups=1, bias=False):
        super(BNAfterDynamicMatMul, self).__init__()
        self.conv1d = torch.nn.Conv1d(10, 20, 3, padding=padding, stride=stride, dilation=dilation, groups=groups,
                                      bias=bias)
        self.fc1 = torch.nn.Linear(24*10, 20)
        self.flatten = torch.nn.Flatten()
        self.conv1d = torch.nn.Conv1d(10, 20, 3, padding=padding, stride=stride, dilation=dilation, groups=groups,
                                      bias=bias)
        self.bn1 = torch.nn.BatchNorm1d(1)

    def forward(self, x):
        x1 = self.conv1d(x)
        x2 = self.fc1(self.flatten(x)).unsqueeze(1)
        x = torch.matmul(x2, x1)
        x = self.bn1(x)
        return x

class MyModel(torch.nn.Module):
    def __init__(self):
        super(MyModel, self).__init__()

        self.conv1 = torch.nn.Conv2d(10, 20, 3)
        self.bn1 = torch.nn.BatchNorm2d(20)
        self.relu1 = torch.nn.ReLU()

        self.conv2 = torch.nn.Conv2d(20, 15, 3)
        self.relu2 = torch.nn.ReLU()

        self.bn2 = torch.nn.BatchNorm2d(15)
        self.conv3 = torch.nn.Conv2d(15, 20, 3)

        self.conv4 = torch.nn.Conv2d(20, 20, 3)
        self.bn3 = torch.nn.BatchNorm2d(20)
        self.bn4 = torch.nn.BatchNorm2d(20)

        self.fc1 = torch.nn.Linear(5120, 10)

    def forward(self, x):
        # Regular case - conv followed by bn
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)

        # Non-linearity between conv and bn, not a candidate for fold
        x = self.conv2(x)
        x = self.relu2(x)

        # Case where BN can fold into an immediate downstream conv
        x = self.bn2(x)
        x = self.conv3(x)

        # No fold if there is a split between conv and BN
        x = self.conv4(x)
        bn1_out = self.bn3(x)
        bn2_out = self.bn4(x)

        x = bn1_out + bn2_out

        x = x.view(x.size(0), -1)
        x = self.fc1(x)

        return x


class MyModelFoldFoward(torch.nn.Module):
    def __init__(self):
        super(MyModelFoldFoward, self).__init__()

        self.conv1 = torch.nn.Conv2d(10, 20, 3)
        self.bn1 = torch.nn.BatchNorm2d(20)
        self.relu1 = torch.nn.ReLU6()

        self.conv2 = torch.nn.Conv2d(20, 15, 3)
        self.bn2 = torch.nn.BatchNorm2d(15)
        self.relu2 = torch.nn.ReLU()

        self.conv3 = torch.nn.Conv2d(15, 20, 3)

    def forward(self, x):
        # Regular case - conv followed by bn
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu1(x)

        # Non-linearity between conv and bn, not a candidate for fold
        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu2(x)

        # Case where BN can fold into an immediate downstream conv
        x = self.conv3(x)

        return x


def _convert_to_onnx_no_fold(model: torch.nn.Module, dummy_input, filename='./temp_model.onnx'):
    torch.onnx.export(model.eval(),
                      dummy_input,
                      filename,
                      training=torch.onnx.TrainingMode.PRESERVE,
                      export_params=True,
                      opset_version=12,
                      do_constant_folding=False,
                      input_names=['input'],
                      output_names=['output'])
    model = ONNXModel(load_model(filename))
    return model


def _convert_to_onnx(model: torch.nn.Module, dummy_input, filename='./temp_model.onnx'):
    torch.onnx.export(model.eval(),
                      dummy_input,
                      filename,
                      training=torch.onnx.TrainingMode.EVAL,
                      export_params=True,
                      opset_version=12,
                      do_constant_folding=True,
                      input_names=['input'],
                      output_names=['output'])
    model = ONNXModel(load_model(filename))
    return model


def my_model_with_bns():
    torch.manual_seed(10)
    model = MyModelFoldFoward().eval()
    initialize_bn_params(model)

    input_shape = (2, 10, 24, 24)
    x = torch.randn(*input_shape, requires_grad=True)

    # Export the model
    torch.onnx.export(model,  # model being run
                      x,  # model input (or a tuple for multiple inputs)
                      "./model_single_residual.onnx",
                      # where to save the model (can be a file or file-like object),
                      training=torch.onnx.TrainingMode.TRAINING,
                      export_params=True,  # store the trained parameter weights inside the model file
                      opset_version=12,  # the ONNX version to export the model to
                      do_constant_folding=False,  # whether to execute constant folding for optimization
                      input_names=['input'],  # the model's input names
                      output_names=['output'])
    model = ONNXModel(load_model('./model_single_residual.onnx'))
    return model


def initialize_bn_params(model: torch.nn.Module):
    for module in model.modules():
        if isinstance(module, _BatchNorm) and module.affine:
            with torch.no_grad():
                module.weight.copy_(torch.randn_like(module.weight))
                module.bias.copy_(torch.randn_like(module.bias))
                module.running_mean.copy_(torch.randn_like(module.bias))
                module.running_var.add_(torch.randn_like(module.bias).abs())

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

""" Optimization code to fold batch-norm layers """

import contextlib
import math
from typing import List, Tuple, Union, Dict, Iterable
import numpy as np
import torch
import torch.nn
from torch.nn.modules.batchnorm import BatchNorm1d, BatchNorm2d
from torch.nn.modules.conv import _ConvTransposeNd

import aimet_common.libpymo as libpymo

from aimet_common.bias_correction import ConvBnPatternHandler
from aimet_common.graph_pattern_matcher import PatternType
from aimet_common.graph_searcher import GraphSearcher
from aimet_common.utils import AimetLogger

# pylint: disable=unused-import
from aimet_torch.defs import PassThroughOp
from aimet_torch import utils
from aimet_torch.meta.connectedgraph import ConnectedGraph
from aimet_torch.quantsim import QuantizationSimModel
from aimet_torch.qc_quantize_op import QcQuantizeWrapper
from aimet_torch.tensor_quantizer import LearnedGridTensorQuantizer

_logger = AimetLogger.get_area_logger(AimetLogger.LogAreas.BatchNormFoldiing)


LayerType = Union[
    torch.nn.Linear,
    torch.nn.Conv1d,
    torch.nn.Conv2d,
    torch.nn.ConvTranspose2d,
]
_supported_layers = LayerType.__args__

BatchNormType = Union[BatchNorm1d, BatchNorm2d]
_supported_batchnorms = BatchNormType.__args__


def _delete_bn_from_model(model: torch.nn.Module, bn_layer_list: Iterable[BatchNormType]):
    utils.replace_modules_with_instances_of_new_type(model, bn_layer_list, torch.nn.Identity)


@contextlib.contextmanager
def _expand_shape_to_4d(weight_tensor: libpymo.TensorParams):
    """ Expand the shape of the weight into 4d.  """
    dims = len(weight_tensor.shape)

    if dims > 5:
        raise RuntimeError

    if dims == 4:
        yield weight_tensor

    else:
        orig_shape = weight_tensor.shape
        if dims < 4:
            # If we have less dimensions, we add 1s to make 4 dimensions
            _4d_shape = np.append(orig_shape, [1 for _ in range(4-dims)]).astype(int)
        else:
            # If we have more dimensions, we concatenate all the dimensions beyond 3 into one dimension
            _4d_shape = np.array(orig_shape[:3] + [math.prod(orig_shape[3:])])

        try:
            weight_tensor.shape = _4d_shape
            yield weight_tensor
        finally:
            weight_tensor.shape = orig_shape


def _call_mo_batch_norm_fold(weight: torch.Tensor,
                             bias: torch.Tensor,
                             bn: BatchNormType,
                             fold_backward: bool):
    """
    Calls C++ batch norm folding API.

    :param weight: Weight or scale tensor to fold BN into.
    :param bias: Bias tensor to fold BN into.
    :param bn: Batch Norm layer
    :param fold_backward: True if BatchNorm comes after Conv/Linear layer
    """
    with torch.no_grad():
        bn_params = libpymo.BNParams()
        bn_params.gamma = bn.weight.detach().cpu().numpy().reshape(-1)
        bn_params.beta = bn.bias.detach().cpu().numpy().reshape(-1)
        bn_params.runningMean = bn.running_mean.detach().cpu().numpy().reshape(-1)
        sigma = torch.sqrt(bn.running_var + bn.eps)
        bn_params.runningVar = sigma.detach().cpu().numpy().reshape(-1)

        weight_tensor = libpymo.TensorParams()

        weight_tensor.data = weight.detach().cpu().numpy().reshape(-1)
        weight_tensor.shape = np.array(weight.shape)

        bias_tensor = libpymo.TensorParams()

        bias_tensor.data = bias.detach().cpu().numpy().reshape(-1)
        bias_tensor.shape = np.array(bias.shape)
        is_bias_valid = True

        with _expand_shape_to_4d(weight_tensor):
            _bias = libpymo.fold(bn_params, weight_tensor, bias_tensor, is_bias_valid, fold_backward)

        bias.copy_(torch.tensor(_bias, device=bias.device, dtype=bias.dtype)
                   .reshape_as(bias))

        weight.copy_(torch.tensor(weight_tensor.data, device=weight.device, dtype=weight.dtype)
                     .reshape_as(weight))


class _BatchNormFoldingNotSupported(RuntimeError):
    pass


def _fold_to_scale(conv_wrapper: QcQuantizeWrapper, bn_wrapper: QcQuantizeWrapper):
    """
    Fold BatchNorm into the scale and bias of the given layer.

    :param conv_wrapper: QcQuantizeWrapper that wraps conv or linear layer.
    :param bn_wrapper: QcQuantizeWrapper that wraps bn.
    """
    # pylint: disable=protected-access, too-many-locals, too-many-branches, bad-whitespace, too-many-statements
    conv = conv_wrapper._module_to_wrap
    bn = bn_wrapper._module_to_wrap

    weight_quantizer = conv_wrapper.param_quantizers["weight"]

    if not isinstance(weight_quantizer, LearnedGridTensorQuantizer):
        raise _BatchNormFoldingNotSupported(
            "BatchNorm folding to scale supports LearnedGridTensorQuantizer only; "
            f"got {type(weight_quantizer)}."
        )

    output_quantizer = conv_wrapper.output_quantizers[0]

    if output_quantizer.enabled:
        raise _BatchNormFoldingNotSupported(
            "BatchNorm should belong to the same supergroup with the layer to be folded to."
        )

    if "bias" in conv_wrapper.param_quantizers:
        bias_quantizer = conv_wrapper.param_quantizers["bias"]
        if bias_quantizer.enabled:
            raise _BatchNormFoldingNotSupported(
                "Can't fold BatchNorm to scale if bias quantizer is enabled."
            )

    encodings = weight_quantizer.encoding

    if encodings is None:
        raise RuntimeError

    if isinstance(encodings, libpymo.TfEncoding):
        encodings = [encodings]

    if isinstance(conv, _ConvTransposeNd) and conv.groups != 1:
        raise _BatchNormFoldingNotSupported(
            "BatchNorm folding to scale is not supported for grouped ConvTransposeNd."
        )

    # Add quantization noise to the BN params (bn weight & bn bias) before folding.
    # NOTE: Quantization of foldable batchnorms is automatically disabled when
    #       initializing quantsim. However, it is still safer to call _quantize_params here
    #       as we can't guarantee this is always the case.
    #       For example, the user can manually enable quantization of batchnorms, etc...
    #       (FYI: _quantize_params takes effect only when the parameter quantizers are enabled)
    with bn_wrapper._quantize_params():
        _fold_to_weight(conv, bn, fold_backward=True)

        gamma = bn.weight
        sigma = torch.sqrt(bn.running_var + bn.eps)

        new_encodings = []
        for old_encoding, c in zip(encodings, gamma/sigma):
            new_encoding = libpymo.TfEncoding()
            new_encoding.delta = old_encoding.delta * abs(c)
            if c >= 0:
                new_encoding.max = old_encoding.max * c
                new_encoding.min = old_encoding.min * c
            else:
                new_encoding.max = old_encoding.min * c
                new_encoding.min = old_encoding.max * c
            new_encoding.offset = old_encoding.offset
            new_encoding.bw = old_encoding.bw
            new_encodings.append(new_encoding)

        weight_quantizer.encoding = new_encodings

    # Copy batchnorm's output quantizers to conv output quantizers
    for conv_output_quantizer, bn_output_quantizer in\
            zip(conv_wrapper.output_quantizers, bn_wrapper.output_quantizers):
        conv_output_quantizer.enabled = bn_output_quantizer.enabled

        if bn_output_quantizer.encoding is not None:
            encoding = libpymo.TfEncoding()
            encoding.delta  = bn_output_quantizer.encoding.delta
            encoding.max    = bn_output_quantizer.encoding.max
            encoding.min    = bn_output_quantizer.encoding.min
            encoding.offset = bn_output_quantizer.encoding.offset
            encoding.bw     = bn_output_quantizer.encoding.bw
            conv_output_quantizer.encoding = encoding

        bn_output_quantizer.enabled = False

    if "bias" not in conv_wrapper.param_quantizers:
        bias_quantizer = LearnedGridTensorQuantizer(weight_quantizer.bitwidth,
                                                    weight_quantizer.round_mode,
                                                    weight_quantizer.quant_scheme,
                                                    weight_quantizer.use_symmetric_encodings,
                                                    enabled_by_default=False,
                                                    data_type=weight_quantizer.data_type)
        bias_quantizer._ch_axis = weight_quantizer._ch_axis
        conv_wrapper.param_quantizers["bias"] = bias_quantizer


def _fold_to_weight(conv_linear: LayerType, bn: BatchNormType, fold_backward: bool):
    """
    Fold BatchNorm into the weight and bias of the given layer.

    :param conv_linear: Conv or linear layer to fold BN into.
    :param bn: BatchNorm to fold.
    """
    # Transpose weights to C, N, H, W from N, C, H, W since axis are flipped for transposed conv
    # However depthwise conv layers are always N, 1, H, W whether transposed-conv or not, so no need to transpose
    if isinstance(conv_linear, torch.nn.ConvTranspose2d) and conv_linear.groups == 1:
        conv_linear.weight.data = conv_linear.weight.data.permute(1, 0, 2, 3)

    if conv_linear.bias is None:
        out_channels = conv_linear.out_features if isinstance(conv_linear, torch.nn.Linear)\
                       else conv_linear.out_channels
        bias = torch.zeros(out_channels,
                           device=conv_linear.weight.device,
                           dtype=conv_linear.weight.dtype)
        conv_linear.bias = torch.nn.Parameter(bias)

    _call_mo_batch_norm_fold(conv_linear.weight, conv_linear.bias, bn, fold_backward=fold_backward)

    # Transpose weight back to N, C, H, W for transposed Conv2D, for non-depthwise layers
    if isinstance(conv_linear, torch.nn.ConvTranspose2d) and conv_linear.groups == 1:
        conv_linear.weight.data = conv_linear.weight.data.permute(1, 0, 2, 3)


def fold_given_batch_norms(model, layer_pairs):
    """
    Fold a given set of batch_norm layers into conv layers

    :param model: Model
    :param layer_pairs: Pairs of conv and batch_norm layers to use for folding
    :return: None
    """
    # pylint: disable=protected-access
    conv_bn_pairs = []
    bn_conv_pairs = []

    def is_batchnorm(module: torch.nn.Module) -> bool:
        if isinstance(module, QcQuantizeWrapper):
            module = module._module_to_wrap
        return isinstance(module, _supported_batchnorms)

    def is_conv_linear(module: torch.nn.Module) -> bool:
        if isinstance(module, QcQuantizeWrapper):
            module = module._module_to_wrap
        return isinstance(module, _supported_layers)

    for x, y in layer_pairs:
        if is_batchnorm(x):
            assert is_conv_linear(y)
            bn = x
            conv = y
            bn_conv_pairs.append((bn, conv))
        else:
            assert is_conv_linear(x)
            assert is_batchnorm(y)
            conv = x
            bn = y
            conv_bn_pairs.append((conv, bn))

    _fold_given_batch_norms(model, conv_bn_pairs, bn_conv_pairs)


def _fold_given_batch_norms(model,
                            conv_bn_pairs: Iterable[Tuple[torch.nn.Module, torch.nn.Module]],
                            bn_conv_pairs: Iterable[Tuple[torch.nn.Module, torch.nn.Module]]):
    """
    Fold a given set of batch_norm layers into conv layers

    :param model: Model
    :param conv_bn_pairs: List of (conv, bn) pairs to fold
    :param bn_conv_pairs: List of (bn, conv) pairs to fold
    :return: None
    """
    # pylint: disable=protected-access
    for bn, conv in bn_conv_pairs:
        if isinstance(conv, QcQuantizeWrapper):
            raise RuntimeError(f"Forward folding to scale is not possible. Got {conv}")

    bn_modules = []

    def _fold(conv, bn, fold_backward):
        is_wrapped = isinstance(conv, QcQuantizeWrapper) or isinstance(bn, QcQuantizeWrapper)
        try:
            if is_wrapped:
                assert isinstance(conv, QcQuantizeWrapper) and isinstance(bn, QcQuantizeWrapper)
                _fold_to_scale(conv, bn)
                bn_modules.append(bn._module_to_wrap)
            else:
                _fold_to_weight(conv, bn, fold_backward=fold_backward)
        except _BatchNormFoldingNotSupported as e:
            bn_name = utils.get_layer_name(model, bn)
            conv_name = utils.get_layer_name(model, conv)
            _logger.warning(
                "Failed to fold %s to %s. [Reason] %s", bn_name, conv_name, str(e)
            )
        else:
            bn_modules.append(bn._module_to_wrap if is_wrapped else bn)


    with utils.in_eval_mode(model), torch.no_grad():
        for conv, bn in conv_bn_pairs:
            _fold(conv, bn, fold_backward=True)

        for bn, conv in bn_conv_pairs:
            _fold(conv, bn, fold_backward=False)

        _delete_bn_from_model(model, bn_modules)


def find_all_batch_norms_to_fold(model, input_shapes, dummy_input: Union[torch.Tensor, Tuple] = None):
    """
    Find all possible batch norm layers that can be folded. And returns a list of pairs such that (bn, layer)
    means bn will be forward-folded into layer and (layer, bn) means bn will be backward-folded into layer
    :param model: Model to search
    :param input_shapes: Input shapes to use for the model (can be one or multiple inputs)
    :param dummy_input: A dummy input to the model. Can be a Tensor or a Tuple of Tensors
    :return: List of pairs of bn and layers to fold bn into
    """
    device = utils.get_device(model)
    inp_tensor_list = utils.create_rand_tensors_given_shapes(input_shapes, device)
    connected_graph = ConnectedGraph(model, inp_tensor_list)
    conv_bn_pairs, bn_conv_pairs = _find_all_batch_norms_to_fold(model, input_shapes, connected_graph, dummy_input)
    return conv_bn_pairs + bn_conv_pairs


def _find_all_batch_norms_to_fold(
        model: torch.nn.Module,
        input_shapes: Union[Tuple, List[Tuple]],
        connected_graph: ConnectedGraph,
        dummy_input: Union[torch.Tensor, Tuple] = None
) -> Tuple[List[Tuple[LayerType, BatchNormType]],
           List[Tuple[BatchNormType, LayerType]]]:
    """
    Find all possible batch norm layers that can be folded. And returns a list of pairs such that (bn, layer)
    means bn will be forward-folded into layer and (layer, bn) means bn will be backward-folded into layer
    :param model: Model to search
    :param input_shapes: Input shapes to use for the model (can be one or multiple inputs)
    :param connected_graph: Connected graph associated with the model.
    :param dummy_input: A dummy input to the model. Can be a Tensor or a Tuple of Tensors
    :return: A list of (layer, bn) pairs and a list of (bn, layer) pairs,
             where `bn` can be folded into to `layer`.
    """
    conv_linear_bn_activation_info_dict = _find_all_conv_bn_with_activation(connected_graph)

    # To mark BN's already picked for backward folding
    bn_picked_for_folding = set()

    ordered_conv_fc_nodes = utils.get_ordered_lists_of_conv_fc(model, input_shapes, dummy_input)

    conv_bn_pairs = []
    # Backward fold is given priority over Forward fold
    for _, module in ordered_conv_fc_nodes:
        if module in conv_linear_bn_activation_info_dict.keys() and _is_valid_bn_fold(module, True):
            bn_info = conv_linear_bn_activation_info_dict[module]
            if bn_info.output_bn and bn_info.output_bn not in bn_picked_for_folding:
                conv_bn_pairs.append((module, bn_info.output_bn.get_module()))
                bn_picked_for_folding.add(bn_info.output_bn)

    bn_conv_pairs = []
    for _, module in ordered_conv_fc_nodes:
        if module in conv_linear_bn_activation_info_dict.keys() and _is_valid_bn_fold(module, False):
            bn_info = conv_linear_bn_activation_info_dict[module]
            if bn_info.input_bn and bn_info.input_bn not in bn_picked_for_folding:
                bn_conv_pairs.append((bn_info.input_bn.get_module(), module))
                bn_picked_for_folding.add(bn_info.input_bn)

    return conv_bn_pairs, bn_conv_pairs


def _is_valid_bn_fold(conv: LayerType, fold_backward: bool) -> bool:
    """
    Determine if a given layer can successfully absorb a BatchNorm given the layer type and parameters
    :param conv: The Conv/Linear layer to fold a BatchNorm into.
    :param fold_backward: True if BatchNorm comes after Conv/Linear layer
    :return: True if a BatchNorm layer can be folded without causing output error.
    """
    valid = True
    if not fold_backward:
        # Cannot fold BN -> Conv with padding. AIMET does not support forward folding to grouped or DW Conv
        if isinstance(conv, (torch.nn.Conv2d, torch.nn.Conv1d, torch.nn.Conv3d)):
            valid &= all(item == 0 for item in conv.padding)
            valid &= conv.groups == 1
        # AIMET does not support forward folding to ConvTranspose
        elif isinstance(conv, torch.nn.ConvTranspose2d):
            valid = False
    else:
        # AIMET does not support backwards folding to grouped ConvTranspose
        if isinstance(conv, torch.nn.ConvTranspose2d):
            valid &= conv.groups in (1, conv.in_channels)
    return valid


def fold_all_batch_norms_to_weight(
        model: torch.nn.Module,
        input_shapes: Union[Tuple, List[Tuple]],
        dummy_input: Union[torch.Tensor, Tuple] = None
) -> List[Tuple[LayerType, BatchNormType]]:
    """
    Fold all batch_norm layers in a model into the weight of the corresponding conv layers

    :param model: Model
    :param input_shapes: Input shapes for the model (can be one or multiple inputs)
    :param dummy_input: A dummy input to the model. Can be a Tensor or a Tuple of Tensors
    :return: A list of pairs of layers [(Conv/Linear, BN layer that got folded)]
    """
    if isinstance(model, torch.nn.DataParallel):
        return fold_all_batch_norms_to_weight(model.module, input_shapes, dummy_input)
    device = utils.get_device(model)
    if dummy_input is None:
        inp_tensor_list = utils.create_rand_tensors_given_shapes(input_shapes, device)
    else:
        inp_tensor_list = dummy_input
    connected_graph = ConnectedGraph(model, inp_tensor_list)
    conv_bn_pairs, bn_conv_pairs = _find_all_batch_norms_to_fold(model, input_shapes, connected_graph, dummy_input)

    _fold_given_batch_norms(model, conv_bn_pairs, bn_conv_pairs)

    return conv_bn_pairs + [(conv, bn) for bn, conv in bn_conv_pairs]


fold_all_batch_norms = fold_all_batch_norms_to_weight


def fold_all_batch_norms_to_scale(
        sim: QuantizationSimModel,
        input_shapes: Union[Tuple, List[Tuple]],
) -> List[Tuple[QcQuantizeWrapper, QcQuantizeWrapper]]:
    """
    Fold all batch_norm layers in a model into the quantization scale parameter
    of the corresponding conv layers

    :param sim: QuantizationSimModel
    :param input_shapes: Input shapes for the model (can be one or multiple inputs)
    :return: A list of pairs of layers [(Conv/Linear, BN layer that got folded)]
    """
    # pylint: disable=protected-access
    assert sim.model is not None
    assert sim.connected_graph is not None

    model = sim.model
    connected_graph = sim.connected_graph

    quant_wrappers = {
        quant_wrapper._module_to_wrap: quant_wrapper
        for _, quant_wrapper in sim.quant_wrappers()
    }
    conv_bn_pairs, bn_conv_pairs = _find_all_batch_norms_to_fold(model, input_shapes, connected_graph)
    conv_bn_pairs = [
        (quant_wrappers[conv], quant_wrappers[bn]) for conv, bn in conv_bn_pairs
    ]
    bn_conv_pairs = [
        (quant_wrappers[bn], quant_wrappers[conv]) for bn, conv in bn_conv_pairs
    ]

    _fold_given_batch_norms(model, conv_bn_pairs, bn_conv_pairs)

    return conv_bn_pairs + [(conv, bn) for bn, conv in bn_conv_pairs]


def find_all_conv_bn_with_activation(model: torch.nn.Module, input_shape: Tuple) -> Dict:
    """
    Uses searcher to find preceding and next bn layers for a conv/linear layer
    :param model: PyTorch model
    :param input_shape: shape of input to the model
    :return: dictionary of conv/linear layers with associated bn op / activation info
    """
    device = utils.get_device(model)
    inp_tensor_list = utils.create_rand_tensors_given_shapes(input_shape, device)
    connected_graph = ConnectedGraph(model, inp_tensor_list)
    return _find_all_conv_bn_with_activation(connected_graph)


def _find_all_conv_bn_with_activation(connected_graph: ConnectedGraph) -> Dict:
    """
    Uses searcher to find preceding and next bn layers for a conv/linear layer
    :param connected_graph: ConnectedGraph object.
    :return: dictionary of conv/linear layers with associated bn op / activation info
    """

    # initialize all patterns to be matched and associated call back functions
    patterns_with_callbacks = []
    layer_select_handler = ConvBnPatternHandler()
    conv_types = ['Conv1d', 'Conv', 'ConvTranspose']
    linear_types = ['Gemm']

    for op_type in conv_types + linear_types:
        patterns_with_callbacks.append(PatternType(pattern=['BatchNormalization', op_type],
                                                   action=layer_select_handler))
        patterns_with_callbacks.append(PatternType(pattern=[op_type, 'BatchNormalization'],
                                                   action=layer_select_handler))
    patterns_with_callbacks.append(PatternType(pattern=['Conv3d', 'BatchNorm3d'], action=layer_select_handler))
    patterns_with_callbacks.append(PatternType(pattern=['BatchNorm3d', 'Conv3d'], action=layer_select_handler))

    # create graph searcher instance with connected graph and patterns to search
    graph_searcher = GraphSearcher(connected_graph, patterns_with_callbacks)

    # get all conv/linear and bn info
    graph_searcher.find_all_patterns_in_graph_apply_actions()
    convs_bn_activation_dict = layer_select_handler.get_conv_linear_bn_info_dict()

    return convs_bn_activation_dict

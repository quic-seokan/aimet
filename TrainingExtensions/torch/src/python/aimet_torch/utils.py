# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2018-2023, Qualcomm Innovation Center, Inc. All rights reserved.
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
""" Utilities that are used for different AIMET PyTorch features """

import importlib
import itertools
from typing import List, Tuple, Union, Dict, Callable, Any, Iterable
import contextlib
import os
import pickle
import sys
import numpy as np
import torch.nn
import torch
from torch.utils.data import DataLoader, Dataset
from torchvision import datasets, transforms

from aimet_common.defs import QuantScheme, QuantizationDataType, MAP_QUANT_SCHEME_TO_PYMO
from aimet_common.utils import AimetLogger, log_with_error_and_assert_if_false
import aimet_common.libpymo as libpymo


logger = AimetLogger.get_area_logger(AimetLogger.LogAreas.Utils)

dtypes_to_ignore_for_quantization = (int, float, bool, str, tuple, type(None))
torch_dtypes_to_ignore_for_quantization = [torch.int, torch.int8, torch.int16, torch.int32, torch.int64, torch.bool]
allowed_output_types = (torch.Tensor, *dtypes_to_ignore_for_quantization)


class IterFirstX:
    """ Iterator for the first x samples in a given data-loader """

    def __init__(self, data_loader, num_samples):
        self.data_loader = data_loader
        self.num_samples = num_samples

    def __iter__(self):
        for i, batch in enumerate(self.data_loader):
            if i >= self.num_samples:
                break
            yield batch


class StopForwardException(Exception):
    """
    Dummy exception to early-terminate forward-pass
    """


class ModuleData:
    """
    Collect input and output data to and from module
    """
    def __init__(self, model: torch.nn.Module, module: torch.nn.Module,
                 forward_fn: Callable[[torch.nn.Module, Any], Any] = None):
        """
        :param model: Pytorch model
        :param module: Module reference
        :param forward_fn: Adapter function that performs forward pass given a model and inputs
         yielded from the data loader.
        """
        self._model = model
        self._module = module
        self._forward_fn = forward_fn or self.default_forward_fn

    def collect_inp_out_data(self, model_input: Union[torch.tensor, List[torch.Tensor], Tuple[torch.Tensor]],
                             collect_input: bool, collect_output: bool) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Collect input and output data depending on the collect_input and collect_output flag

        :param model_input: Input to model, Can be a single tensor or a list/tuple of tensors
        :param collect_input: Boolean to collect input or not
        :param collect_output: Boolean to collect output or not
        :return: Module's input and output data
        """
        def _hook_to_collect_inp_out_data(_, inp, out):
            """
            hook to collect input and output data
            """
            if collect_input:
                inp_data_list.append(inp[0])

            if collect_output:
                out_data_list.append(out)

            raise StopForwardException

        inp_data_list = []
        out_data_list = []

        handle = self._module.register_forward_hook(_hook_to_collect_inp_out_data)

        # get the model's device placement information
        device = get_device(self._model)

        # place the input to appropriate device
        model_input = change_tensor_device_placement(model_input, device)

        # Custom injected exception is raised when the activations data from desired module is collected.
        try:
            with in_eval_mode(self._model), torch.no_grad():
                _ = self._forward_fn(self._model, model_input)
        except StopForwardException:
            pass

        # remove hook handle
        handle.remove()

        inp_data, out_data = None, None

        if inp_data_list and isinstance(inp_data_list[0], torch.Tensor):
            inp_data = inp_data_list[0].detach()

        if out_data_list and isinstance(out_data_list[0], torch.Tensor):
            out_data = out_data_list[0].detach()

        return inp_data, out_data

    @staticmethod
    def default_forward_fn(model: torch.nn.Module,
                           inputs: Union[torch.tensor, List[torch.Tensor], Tuple[torch.Tensor]]):
        """
        Default forward function that performs forward pass given a model and inputs yielded from
        the data loader. Data loader which yields torch.Tensor object that can be directly
        passed into the model, or a data loader which yields a tuple of length two where its
        first element can be directly passed into the model.

        :param model: PyTorch model.
        :param inputs: Inputs passed to model.
        """
        # When provided dataloader is labeled (model_inputs, labels), then ignore the second element (labels).
        if isinstance(inputs, (list, tuple)):
            inputs, _ = inputs
        if isinstance(inputs, torch.Tensor):
            inputs = [inputs]
        model(*inputs)


class CachedDataset(Dataset):
    """
    Cache number of batches from the data loader at given path location and
    provide interface to fetch single batch of model inputs.
    """

    # pylint: disable=super-init-not-called
    def __init__(self, data_loader: DataLoader, num_batches: int, path: str):
        """
        :param data_loader: Data loader
        :param num_batches: Number of batches to fetch from data loader
        :param path: Path to save model inputs
        """
        if len(data_loader) < num_batches:
            raise ValueError(f'Can not fetch {num_batches} batches from '
                             f'a data loader of length {len(data_loader)}.')

        self._num_batches = num_batches
        self._path = path

        self._cache_model_inputs(itertools.islice(data_loader, num_batches))

    def __len__(self):
        return self._num_batches

    def __getitem__(self, index: int):
        path = os.path.join(self._path, 'model_inputs_' + str(index))

        with open(path, 'rb') as file:
            batch = pickle.load(file)

        return batch

    def _cache_model_inputs(self, data_loader):
        """
        Function to cache number of batches individually in separate file at provided path location
        """
        if not os.path.exists(self._path):
            os.makedirs(self._path)

        for i, batch in enumerate(data_loader):
            path = os.path.join(self._path, f'model_inputs_{i}')
            with open(path, 'wb') as file:
                pickle.dump(batch, file)

        logger.info('Caching %d batches from data loader at path location: %s', self._num_batches, self._path)


def run_hook_for_layers(model: torch.nn.Module, input_shapes: Union[Tuple, List[Tuple]], hook,
                        module_type_for_attaching_hook=None, leaf_node_only=True):
    """
    Register the given hook function for all layers in the model
    :param model: Model
    :param input_shapes: Shape of inputs to pass to the model
    :param hook: Hook function to register
    :param module_type_for_attaching_hook: Tuple of torch.nn module types for which hook has to be attached
    :param leaf_node_only: Set to False if all modules are required
    :return: None
    """

    # ------------------------
    # Register hook function
    # ------------------------
    hooks = []
    # All leaf modules
    modules = [module for module in model.modules() if not leaf_node_only or is_leaf_module(module)]
    if module_type_for_attaching_hook:
        # if needed, filter by module types specified by caller
        modules = [module for module in modules if isinstance(module, module_type_for_attaching_hook)]
    for module in modules:
        hooks.append(module.register_forward_hook(hook))

    # ------------------------------------------------
    # Run forward pass to execute the hook functions
    # ------------------------------------------------
    device = get_device(model)
    dummy_tensors = create_rand_tensors_given_shapes(input_shapes, device)
    with in_eval_mode(model), torch.no_grad():
        _ = model(*dummy_tensors)

    # --------------------------
    # Remove all hooks we added
    # --------------------------
    for h in hooks:
        h.remove()


def run_hook_for_layers_with_given_input(model: torch.nn.Module,
                                         input_tensor: Union[torch.Tensor, Tuple],
                                         hook, module_type_for_attaching_hook=None, leaf_node_only=True):
    """
    Register the given hook function for all layers in the model
    :param model: Model
    :param input_tensor: Input tensor to the model. If more than one model inputs, use a tuple
    :param hook: Hook function to register
    :param module_type_for_attaching_hook: Tuple of torch.nn module types for which hook has to be attached
    :param leaf_node_only: Set to False if all modules are required
    :return: None
    """

    # ------------------------
    # Register hook function
    # ------------------------
    hooks = []
    # All leaf modules
    modules = [module for module in model.modules() if not leaf_node_only or is_leaf_module(module)]
    if module_type_for_attaching_hook:
        # if needed, filter by module types specified by caller
        modules = [module for module in modules if isinstance(module, module_type_for_attaching_hook)]

    try:
        for module in modules:
            hooks.append(module.register_forward_hook(hook))

        # ------------------------------------------------
        # Run forward pass to execute the hook functions
        # ------------------------------------------------
        with in_eval_mode(model), torch.no_grad():
            if isinstance(input_tensor, (list, tuple)):
                _ = model(*input_tensor)
            else:
                _ = model(input_tensor)

    finally:
        # --------------------------
        # Remove all hooks we added
        # --------------------------
        for h in hooks:
            h.remove()


def to_numpy(tensor: torch.Tensor):
    """
     Helper function that turns the given tensor into a numpy array
    :param tensor       : torch.Tensor
    :return             : float or np.array
    """

    if isinstance(tensor, np.ndarray):
        return tensor

    # if tensor is allocated on GPU, first copy to CPU
    # then detach from the current graph and convert to numpy array
    if hasattr(tensor, 'is_cuda'):
        if tensor.is_cuda:
            return tensor.cpu().detach().numpy()

    # if tensor is on CPU only
    if hasattr(tensor, 'detach'):
        return tensor.detach().numpy()

    if hasattr(tensor, 'numpy'):
        return tensor.numpy()

    return np.array(tensor)


def create_fake_data_loader(dataset_size: int, batch_size: int, image_size=(1, 28, 28)):
    """
    Helper function to create fake data loader which is default image size (1, 28, 28)
    :param dataset_size     : total images in data set
    :param batch_size       : batch size
    :param image_size       : size of input
    :return:
    """
    transform = transforms.Compose([transforms.ToTensor()])
    data_loader = torch.utils.data.DataLoader(datasets.FakeData(size=dataset_size, image_size=image_size,
                                                                num_classes=10, transform=transform,
                                                                target_transform=None),
                                              batch_size=batch_size, shuffle=False)
    return data_loader


def get_module_to_name_dict(model: torch.nn.Module, prefix: str = '') -> Dict[torch.nn.Module, str]:
    """
    Get a dictionary mapping model modules to names
    :param model: Model to get mapping for
    :param prefix: Prefix string to prepend to names
    :return: Dictionary mapping model modules to names
    """
    module_to_name_dict = {}
    for name, module in model.named_modules(prefix=prefix):
        module_to_name_dict[module] = name
    return module_to_name_dict


def get_layer_name(model, layer):
    """
    Helper function to get layer name given model and layer reference
    :param model: model (nn.Module)
    :param layer: layer reference
    :return:
    """
    for name, module in model.named_modules():
        if module is layer:
            return name
    raise KeyError(f"Couldn't find layer {layer} from model {model}")


def get_layer_by_name(model, layer_name):
    """
    Helper function to get layer reference given layer name
    :param model        : model (nn.Module)
    :param layer_name   : layer_name
    :return:
    """
    try:
        return dict(model.named_modules())[layer_name]
    except KeyError as e:
        raise KeyError(f"Couldn't find layer named {layer_name}") from e


def is_model_on_gpu(model):
    """
    Function to check whether given model is created on GPU or CPU
    Assumption : model is on single device
    :return:
        True if the model is on GPU, False if on CPU
    """
    return next(model.parameters()).is_cuda


def get_device(model):
    """
    Function to find which device is model on
    Assumption : model is on single device
    :param model:
    :return: Device on which model is present
    """
    return next(model.parameters()).device

def match_model_settings(model_to_match: torch.nn.Module, model_to_set: torch.nn.Module):
    """
    Match training and device settings of the model_to_set with those of model_to_match.

    :param model_to_match: Model to match settings for
    :param model_to_set: Model to set
    """
    model_to_set.train(model_to_match.training)
    if get_device(model_to_set) != get_device(model_to_match):
        model_to_set.to(get_device(model_to_match))


def load_pytorch_model(model_name: str, path: str, filename: str, load_state_dict: bool = False) -> torch.nn.Module:
    """
    Load the pytorch model from the given path and filename.
    NOTE: The model can only be saved by saving the state dict. Attempting to serialize the entire model will result
    in a mismatch between class types of the model defined and the class type that is imported programatically.

    :param model_name: Name of model
    :param path: Path where the pytorch model definition file is saved
    :param filename: Filename of the pytorch model definition
    :param load_state_dict: If True, load state dict with the given path and filename. The state dict file is expected
        to end in '.pth'
    :return: Imported pytorch model
    """

    model_path = os.path.join(path, filename + '.py')
    if not os.path.exists(model_path):
        logger.error('Unable to find model file at path %s', model_path)
        raise AssertionError('Unable to find model file at path ' + model_path)

    # Import model's module and instantiate model
    spec = importlib.util.spec_from_file_location(filename, model_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[model_name] = module
    spec.loader.exec_module(module)
    model = getattr(module, model_name)()

    # Load state dict if necessary
    if load_state_dict:
        state_dict_path = os.path.join(path, filename + '.pth')
        if not os.path.exists(state_dict_path):
            logger.error('Unable to find state dict file at path %s', state_dict_path)
            raise AssertionError('Unable to find state dict file at path ' + state_dict_path)
        model.load_state_dict(torch.load(state_dict_path))

    return model

def is_leaf_module(module):

    """Utility function to determine if the given module is a leaf module - that is, does not have children modules
    :return:
        True if the module is a leaf, False otherwise
    """
    module_list = list(module.modules())

    return bool(len(module_list) == 1)


def get_input_shape_batch_size(data_loader):
    """
    Gets input shape of image and batch size from data loader
    :param data_loader: Iterates over data set
    :return: returns batch size and shape of one image
    """
    for _, (images_in_one_batch, *_) in enumerate(data_loader):
        # finding shape of a batch
        input_shape = torch.Tensor.size(images_in_one_batch)

        return input_shape[0], (1, input_shape[1], input_shape[2], input_shape[3])


def has_hooks(module: torch.nn.Module):
    """ Returns True if the module uses hooks. """

    for hooks in (module._forward_pre_hooks,                       # pylint: disable=protected-access
                  module._forward_hooks, module._backward_hooks):  # pylint: disable=protected-access
        if hooks:
            logger.warning("The specified model has registered hooks which might break winnowing")
            return True
    return False


def get_one_positions_in_binary_mask(mask):
    """
    Return the indices of one positions in a binary mask.

    :param mask: a mask that contains either 0s or 1s
    :return:
    """

    mask_one_positions = [i for i in range(len(mask)) if mask[i] == 1]
    return mask_one_positions


def get_ordered_list_of_modules(model: torch.nn.Module,
                                dummy_input: Union[torch.Tensor, List[torch.Tensor], Tuple]) -> List:
    """
    Finds ordered modules in given model.
    :param model: PyTorch model.
    :param dummy_input: Dummy input to the model. Used to parse model graph.
    :return: List of module name, module in order.
    """
    def _hook_to_collect_name_of_module(module, _, __):
        """
        hook to find name of module
        """
        module_name = module_to_name_dict[module]
        list_modules.append([module_name, module])

    module_to_name_dict = {}
    for name, module in model.named_modules():
        module_to_name_dict[module] = name

    list_modules = []
    run_hook_for_layers_with_given_input(model, dummy_input, hook=_hook_to_collect_name_of_module)

    return list_modules


def replace_modules_of_type1_with_type2(model: torch.nn.Module,
                                        type1: type(torch.nn.Module), type2: type(torch.nn.Module)):
    """
    Given a model, finds all modules of type type1 and replaces them with instances of type2
    Note: Since instances of type2 are instantiated using a default constructor (no parameters),
    only certain module types e.g. torch.nn.ReLU can be used as type2
    :param model: Model to replace modules in
    :param type1: Module type of modules to replace
    :param type2: Module type to instantiate to replace modules with
    :return: None
    """

    for module_name, module_ref in model.named_children():
        if isinstance(module_ref, type1):
            setattr(model, module_name, type2())

        children_module_list = list(module_ref.modules())
        if len(children_module_list) != 1:
            replace_modules_of_type1_with_type2(module_ref, type1, type2)


def replace_modules_of_type1_using_constructor(model, type1, constructor):
    """
    Given a model, finds all modules of type type1 and replaces them with the module created with constructor
    constructor should accept original module as an argument
    :param model: Model to replace modules in
    :param type1: Module type of modules to replace
    :param constructor: Constructor of the new module
    :return: None
    """

    for module_name, module_ref in model.named_children():
        if isinstance(module_ref, type1):
            setattr(model, module_name, constructor(module_ref))

        children_module_list = list(module_ref.modules())
        if len(children_module_list) != 1:
            replace_modules_of_type1_using_constructor(module_ref, type1, constructor)


def replace_modules_with_instances_of_new_type(model: torch.nn.Module, modules_to_replace_list: List[torch.nn.Module],
                                               new_type: type(torch.nn.Module)):
    """
    Given a model, replaces given modules with instances of new_type
    Note: Since instances of new_type are instantiated using a default constructor (no parameters),
    only certain module types e.g. torch.nn.ReLU can be used as new_type
    :param model: Model to replace modules in
    :param modules_to_replace_list: Modules to replace
    :param new_type: Module type to instantiate to replace modules with
    :return: None
    """

    for module_name, module_ref in model.named_children():

        if module_ref in modules_to_replace_list:
            setattr(model, module_name, new_type())

        children_module_list = list(module_ref.modules())
        if len(children_module_list) != 1:
            replace_modules_with_instances_of_new_type(module_ref, modules_to_replace_list, new_type)


def create_rand_tensors_given_shapes(input_shape: Union[Tuple, List[Tuple]], device: torch.device) \
        -> List[torch.Tensor]:
    """
    Given shapes of some tensors, create one or more random tensors and return them as a list of tensors
    :param input_shape: Shapes of tensors to create
    :param device: Device to create tensors on
    :return: Created list of tensors
    """
    if isinstance(input_shape, List):
        input_shapes = input_shape
    else:
        input_shapes = [input_shape]

    rand_tensors = []
    for shape in input_shapes:
        rand_tensors.append(torch.rand(shape).to(device))

    return rand_tensors


def get_ordered_lists_of_conv_fc(model: torch.nn.Module, input_shapes: Tuple,
                                 dummy_input: Union[torch.Tensor, Tuple] = None) -> List:
    """
    Finds order of nodes in graph
    :param model: model
    :param input_shapes: input shape to model
    :param dummy_input: A dummy input to the model. Can be a Tensor or a Tuple of Tensors
    :return: List of names in graph in order
    """

    device = get_device(model)
    if dummy_input is None:
        dummy_input = create_rand_tensors_given_shapes(input_shapes, device)
    module_list = get_ordered_list_of_modules(model, dummy_input)
    module_list = [[name, module] for name, module in module_list if
                   isinstance(module, (torch.nn.Conv1d, torch.nn.Conv2d, torch.nn.Linear, torch.nn.ConvTranspose2d,
                                       torch.nn.Conv3d))]
    return module_list


def change_tensor_device_placement(tensor_data: Union[torch.Tensor, List, Tuple], device: torch.device):
    """
    Change the tensor_data's device placement

    :param tensor_data: torch.tensor , list of torch.tensors, or tuple of torch.tensors
    :param device: device information
    :return: tensor_data with modified device placement
    """
    if isinstance(tensor_data, torch.Tensor):
        return tensor_data.to(device=device)

    if isinstance(tensor_data, (tuple, list)):
        cls = tuple if isinstance(tensor_data, tuple) else list
        return cls(change_tensor_device_placement(x, device) for x in tensor_data)

    return tensor_data


def find_num_inout_tensors_per_module(model: torch.nn.Module, input_tensor) -> Dict:
    """
    Returns a map of module -> number of output tensors, for all the children modules of the
    provided module

    :param model: Torch module to find children modules for
    :param input_tensor: Input tensor to use to run forward pass for the model. If model needs more than one input
                         tensor, pass a tuple
    :return: map of module -> number of output tensors
    """

    num_inout_map = {}

    def record_num_outputs(module, inputs, outputs):
        num_inputs = len(inputs) if isinstance(inputs, (List, Tuple)) else 1
        num_outputs = len(outputs) if isinstance(outputs, (List, Tuple)) else 1
        num_inout_map[module] = (num_inputs, num_outputs)

    run_hook_for_layers_with_given_input(model, input_tensor, record_num_outputs)
    return num_inout_map


def create_encoding_from_dict(encoding_dict: dict) -> (libpymo.TfEncoding, bool):
    """
    Create encoding object from encoding dictionary
    :param encoding_dict: Dictionary containing encodings
    :return: Encoding object, is_symmetric
    """
    encoding = libpymo.TfEncoding()
    encoding.bw = encoding_dict.get('bitwidth')
    encoding.max = encoding_dict.get('max')
    encoding.min = encoding_dict.get('min')
    encoding.delta = encoding_dict.get('scale')
    encoding.offset = encoding_dict.get('offset')
    log_with_error_and_assert_if_false(encoding_dict.get('is_symmetric') in ['True', 'False'],
                                       logger,
                                       f'Unexpected value for is_symmetric: {encoding_dict.get("is_symmetric")}')
    is_symmetric = encoding_dict.get('is_symmetric') == 'True'
    return encoding, is_symmetric


def compute_encoding_for_given_bitwidth(data: np.ndarray, bitwidth: int, quant_scheme: QuantScheme,
                                        is_symmetric: bool, data_type: QuantizationDataType) -> Dict:
    """
    Return encoding dictionary for given bitwidth
    :param data: Numpy data
    :param bitwidth: bitwidth (4-31) to use for quantizing data
    :param quant_scheme: Quantization scheme
    :param is_symmetric: True if symmetric encodings is used, False otherwise
    :return: Encoding Dictionary
    """
    # Create Encodings Analyzer and collect statistical data to compute encodings
    # Since the data is numpy array and on CPU memory, useCuda is False
    encoding_analyzer = libpymo.EncodingAnalyzerForPython(MAP_QUANT_SCHEME_TO_PYMO[quant_scheme])
    encoding_analyzer.updateStats(data, False)

    encoding, is_encoding_valid = encoding_analyzer.computeEncoding(bitwidth, is_symmetric, False, False)

    if is_encoding_valid:
        return {'min': encoding.min,
                'max': encoding.max,
                'scale': encoding.delta,
                'offset': encoding.offset,
                'bitwidth': encoding.bw,
                'is_symmetric': str(is_symmetric),
                'dtype': 'int' if data_type == QuantizationDataType.int else 'float'}

    return {}


def get_reused_modules(model: torch.nn.Module, model_input: Union[torch.Tensor, Tuple]) -> \
        List[Tuple[str, torch.nn.Module]]:
    """
    Identify modules which are used more than once in the model
    :param model: Model to check for modules used more than once
    :param model_input: Input to the model
    :return: List of tuples of name and module for modules in the model which are used more than once
    """
    module_set = set()
    reused_modules_set = set()

    def forward_hook(curr_module, _, _1):
        """
        Custom forward hook function to add modules to module_set and reused_module_set.
        :param curr_module: Current module being traversed during forward pass.
        :param _1: Unused param
        """
        if curr_module in module_set:
            reused_modules_set.add(curr_module)
        else:
            module_set.add(curr_module)

    run_hook_for_layers_with_given_input(model, model_input, forward_hook)

    reused_modules_list = []
    for name, module in model.named_modules():
        if is_leaf_module(module) and module in reused_modules_set:
            reused_modules_list.append((name, module))
    return reused_modules_list


@contextlib.contextmanager
def in_eval_mode(module: Union[torch.nn.Module, Iterable[torch.nn.Module]]):
    """
    Utility to temporarily put model in eval mode using context manager.
    :param module: PyTorch module or a list of modules
    :return: None
    """
    with _in_mode(module, train=False):
        yield


@contextlib.contextmanager
def in_train_mode(module: Union[torch.nn.Module, Iterable[torch.nn.Module]]):
    """
    Utility to temporarily put model in train mode using context manager.
    :param module: PyTorch module or a list of modules
    :return: None
    """
    with _in_mode(module, train=True):
        yield


@contextlib.contextmanager
def _in_mode(modules: Union[torch.nn.Module, Iterable[torch.nn.Module]], train: bool):
    if isinstance(modules, torch.nn.Module):
        modules = [modules]

    original_modes = [module.training for module in modules]

    try:
        for module in modules:
            module.train(mode=train)
        yield
    finally:
        for module, original_mode in zip(modules, original_modes):
            module.train(mode=original_mode)


def is_torch_nn_module(module: torch.nn.Module) -> bool:
    """
    Utility function to determine if the given module is from torch.nn class or not.
    For modules like torch.nn.Conv2d, the utility will return True.

    :param module: PyTorch module.
    :return: True if the module from torch.nn class, False otherwise
    """
    torch_nn_module = False
    if "torch.nn" in str(module.__class__):
        torch_nn_module = True
    return torch_nn_module


def is_torch_nn_leaf_module(module: torch.nn.Module) -> bool:
    """
    Utility function to determine if the given module is leaf and from torch.nn class or not.
    :param module: PyTorch module.
    :return: True if the module is leaf and from torch.nn class, False otherwise
    """
    torch_nn_leaf_module = False
    if is_leaf_module(module) and is_torch_nn_module(module):
        torch_nn_leaf_module = True
    return torch_nn_leaf_module


def is_custom_leaf_module(module: torch.nn.Module, nodes: List[torch._C.Node]) -> bool:
    """
    Given PyTorch module, determine whether the module is leaf module and has not more than one aten node(s).

    :param module: PyTorch module.
    :param nodes: List of trace graph nodes if node.kind() starts with "aten::".
    :return: True if module is custom leaf module, False otherwise.
    """
    # pylint: disable=protected-access
    return is_leaf_module(module) and len(nodes) <= 1


def get_torch_tensortype_shape(torch_graph_output: torch._C.TensorType) -> Union[None, List[int]]:
    """
    Given an output tensor from a torch graph, return its shape, or return None if the output tensor is not a
    tensortype.
    """
    # pylint: disable=protected-access
    shape = None
    if isinstance(torch_graph_output.type(), torch._C.TensorType):
        shape = torch_graph_output.type().sizes()
    return shape


def get_all_quantizers(model: torch.nn.Module):
    """
    Get all the quantizers in the model
    :param model: Root module
    :returns: List of parameter, input, and output quantizers
    """
    from aimet_torch.qc_quantize_op import QcQuantizeWrapper
    from aimet_torch.qc_quantize_recurrent import QcQuantizeRecurrent

    param_quantizers = []
    input_quantizers = []
    output_quantizers = []

    quant_wrappers = [
        m for m in model.modules() if isinstance(m, (QcQuantizeWrapper, QcQuantizeRecurrent))
    ]
    for quant_wrapper in quant_wrappers:
        param_quantizers.extend(quant_wrapper.param_quantizers.values())
        input_quantizers.extend(quant_wrapper.input_quantizers)
        output_quantizers.extend(quant_wrapper.output_quantizers)

    return param_quantizers, input_quantizers, output_quantizers

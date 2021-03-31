# Copyright (c) Facebook, Inc. and its affiliates.
"""
The processors exist in Pythia to make data processing pipelines in various
datasets as similar as possible while allowing code reuse.

The processors also help maintain proper abstractions to keep only what matters
inside the dataset's code. This allows us to keep the dataset ``get_item``
logic really clean and no need about maintaining opinions about data type.
Processors can work on both images and text due to their generic structure.

To create a new processor, follow these steps:

1. Inherit the ``BaseProcessor`` class.
2. Implement ``_call`` function which takes in a dict and returns a dict with
   same keys preprocessed as well as any extra keys that need to be returned.
3. Register the processor using ``@registry.register_processor('name')`` to
   registry where 'name' will be used to refer to your processor later.

In processor's config you can specify ``preprocessor`` option to specify
different kind of preprocessors you want in your dataset.

Let's break down processor's config inside a dataset (VQA2.0) a bit to understand
different moving parts.

Config::


    dataset_attributes:
        vqa2:
            processors:
                text_processor:
                type: vocab
                params:
                    max_length: 14
                    vocab:
                    type: intersected
                    embedding_name: glove.6B.300d
                    vocab_file: vocabs/vocabulary_100k.txt
                    answer_processor:
                    type: vqa_answer
                    params:
                        num_answers: 10
                        vocab_file: vocabs/answers_vqa.txt
                        preprocessor:
                        type: simple_word
                        params: {}

``BaseDataset`` will init the processors and they will available inside your
dataset with same attribute name as the key name, for e.g. `text_processor` will
be available as `self.text_processor` inside your dataset. As is with every module
in Pythia, processor also accept a ``ConfigNode`` with a `type` and `params`
attributes. `params` defined the custom parameters for each of the processors.
By default, processor initialization process will also init `preprocessor` attribute
which can be a processor config in itself. `preprocessor` can be then be accessed
inside the processor's functions.

Example::

    from pythia.common.registry import registry
    from pythia.datasets.processors import BaseProcessor


    class MyProcessor(BaseProcessor):
        def __init__(self, config, *args, **kwargs):
            return

        def __call__(self, item, *args, **kwargs):
            text = item['text']
            text = [t.strip() for t in text.split(" ")]
            return {"text": text}
"""
import multiprocessing
import os
import warnings
from collections import Counter, defaultdict

import numpy as np
import torch

from pytorch_transformers.tokenization_bert import BertTokenizer

from pythia.common.registry import registry
from pythia.utils.configuration import ConfigNode
from pythia.utils.distributed_utils import is_main_process, synchronize
from pythia.utils.general import get_pythia_root
from pythia.utils.text_utils import VocabDict
from pythia.utils.vocab import Vocab, WordToVectorDict
from pythia.utils.phoc import build_phoc

import editdistance

class BaseProcessor:
    """Every processor in Pythia needs to inherit this class for compatability
    with Pythia. End user mainly needs to implement ``__call__`` function.

    Args:
        config (ConfigNode): Config for this processor, containing `type` and
                             `params` attributes if available.

    """

    def __init__(self, config, *args, **kwargs):
        return

    def __call__(self, item, *args, **kwargs):
        """Main function of the processor. Takes in a dict and returns back
        a dict

        Args:
            item (Dict): Some item that needs to be processed.

        Returns:
            Dict: Processed dict.

        """
        return item


class Processor:
    """Wrapper class used by Pythia to initialized processor based on their
    ``type`` as passed in configuration. It retrieves the processor class
    registered in registry corresponding to the ``type`` key and initializes
    with ``params`` passed in configuration. All functions and attributes of
    the processor initialized are directly available via this class.

    Args:
        config (ConfigNode): ConfigNode containing ``type`` of the processor to
                             be initialized and ``params`` of that procesor.

    """

    def __init__(self, config, *args, **kwargs):
        self.writer = registry.get("writer")

        if not hasattr(config, "type"):
            raise AttributeError(
                "Config must have 'type' attribute to specify type of processor"
            )

        processor_class = registry.get_processor_class(config.type)

        params = {}
        if not hasattr(config, "params"):
            self.writer.write(
                "Config doesn't have 'params' attribute to "
                "specify parameters of the processor "
                "of type {}. Setting to default \{\}".format(config.type)
            )
        else:
            params = config.params

        self.processor = processor_class(params, *args, **kwargs)

        self._dir_representation = dir(self)

    def __call__(self, item, *args, **kwargs):
        return self.processor(item, *args, **kwargs)

    def __getattr__(self, name):
        if name in self._dir_representation:
            return getattr(self, name)
        elif hasattr(self.processor, name):
            return getattr(self.processor, name)
        else:
            raise AttributeError(name)


@registry.register_processor("vocab")
class VocabProcessor(BaseProcessor):
    """Use VocabProcessor when you have vocab file and you want to process
    words to indices. Expects UNK token as "<unk>" and pads sentences using
    "<pad>" token. Config parameters can have ``preprocessor`` property which
    is used to preprocess the item passed and ``max_length`` property which
    points to maximum length of the sentence/tokens which can be convert to
    indices. If the length is smaller, the sentence will be padded. Parameters
    for "vocab" are necessary to be passed.

    **Key**: vocab

    Example Config::

        task_attributes:
            vqa:
                vqa2:
                    processors:
                      text_processor:
                        type: vocab
                        params:
                          max_length: 14
                          vocab:
                            type: intersected
                            embedding_name: glove.6B.300d
                            vocab_file: vocabs/vocabulary_100k.txt

    Args:
        config (ConfigNode): node containing configuration parameters of
                             the processor

    Attributes:
        vocab (Vocab): Vocab class object which is abstraction over the vocab
                       file passed.
    """

    MAX_LENGTH_DEFAULT = 50
    PAD_TOKEN = "<pad>"
    PAD_INDEX = 0

    def __init__(self, config, *args, **kwargs):
        if not hasattr(config, "vocab"):
            raise AttributeError(
                "config passed to the processor has no attribute vocab"
            )

        self.vocab = Vocab(*args, **config.vocab, **kwargs)
        self._init_extras(config)

    def _init_extras(self, config, *args, **kwargs):
        self.writer = registry.get("writer")
        self.preprocessor = None

        if hasattr(config, "max_length"):
            self.max_length = config.max_length
        else:
            warnings.warn(
                "No 'max_length' parameter in Processor's "
                "configuration. Setting to {}.".format(self.MAX_LENGTH_DEFAULT)
            )
            self.max_length = self.MAX_LENGTH_DEFAULT

        if hasattr(config, "preprocessor"):
            self.preprocessor = Processor(config.preprocessor, *args, **kwargs)

            if self.preprocessor is None:
                raise ValueError(
                    "No text processor named {} is defined.".format(config.preprocessor)
                )

    def __call__(self, item):
        """Call requires item to have either "tokens" attribute or either
        "text" attribute. If "text" is present, it will tokenized using
        the preprocessor.

        Args:
            item (Dict): Dict containing the "text" or "tokens".

        Returns:
            Dict: Dict containing indices in "text" key, "tokens" in "tokens"
                  key and "length" of the string in "length" key.

        """
        indices = None
        if not isinstance(item, dict):
            raise TypeError(
                "Argument passed to the processor must be "
                "a dict with either 'text' or 'tokens' as "
                "keys"
            )
        if "tokens" in item:
            tokens = item["tokens"]
            indices = self._map_strings_to_indices(item["tokens"])
        elif "text" in item:
            if self.preprocessor is None:
                raise AssertionError(
                    "If tokens are not provided, a text "
                    "processor must be defined in the config"
                )

            tokens = self.preprocessor({"text": item["text"]})["text"]
            indices = self._map_strings_to_indices(tokens)
        else:
            raise AssertionError(
                "A dict with either 'text' or 'tokens' keys "
                "must be passed to the processor"
            )

        tokens, length = self._pad_tokens(tokens)

        return {"text": indices, "tokens": tokens, "length": length}

    def _pad_tokens(self, tokens):
        padded_tokens = [self.PAD_TOKEN] * self.max_length
        token_length = min(len(tokens), self.max_length)
        padded_tokens[:token_length] = tokens[:token_length]
        token_length = torch.tensor(token_length, dtype=torch.long)
        return padded_tokens, token_length

    def get_pad_index(self):
        """Get index of padding <pad> token in vocabulary.

        Returns:
            int: index of the padding token.

        """
        return self.vocab.get_pad_index()

    def get_vocab_size(self):
        """Get size of the vocabulary.

        Returns:
            int: size of the vocabulary.

        """
        return self.vocab.get_size()

    def _map_strings_to_indices(self, tokens):
        length = min(len(tokens), self.max_length)
        tokens = tokens[:length]

        output = torch.zeros(self.max_length, dtype=torch.long)
        output.fill_(self.vocab.get_pad_index())

        for idx, token in enumerate(tokens):
            output[idx] = self.vocab.stoi[token]

        return output


@registry.register_processor("glove")
class GloVeProcessor(VocabProcessor):
    """Inherits VocabProcessor, and returns GloVe vectors for each of the
    words. Maps them to index using vocab processor, and then gets GloVe vectors
    corresponding to those indices.

    Args:
        config (ConfigNode): Configuration parameters for GloVe same as
                             :func:`~VocabProcessor`.

    """

    def __init__(self, config, *args, **kwargs):
        if not hasattr(config, "vocab"):
            raise AttributeError(
                "Config passed to the processor has no attribute vocab"
            )
        vocab_processor_config = ConfigNode(config)
        # GloVeProcessor needs vocab type to be "intersected"
        vocab_processor_config.vocab.type = "intersected"

        if "vocab_file" not in vocab_processor_config.vocab:
            warnings.warn(
                "'vocab_file' key is not present in the config."
                " Switching to pretrained vocab."
            )

            vocab_processor_config.vocab.type = "pretrained"

        super().__init__(vocab_processor_config, *args, **kwargs)

    def __call__(self, item):
        indices = super().__call__(item)["text"]
        embeddings = torch.zeros(
            (len(indices), self.vocab.get_embedding_dim()), dtype=torch.float
        )

        for idx, index in enumerate(indices):
            embeddings[idx] = self.vocab.vectors[index]

        return {"text": embeddings}


@registry.register_processor("fasttext")
class FastTextProcessor(VocabProcessor):
    """FastText processor, similar to GloVe processor but returns FastText vectors.

    Args:
        config (ConfigNode): Configuration values for the processor.

    """

    def __init__(self, config, *args, **kwargs):
        self._init_extras(config)
        self.config = config
        self._download_initially = config.get("download_initially", True)
        self._already_downloaded = False

        if self._download_initially:
            self._try_download()

    def _try_download(self):
        _is_main_process = is_main_process()

        if self._already_downloaded:
            return

        if _is_main_process:
            self.writer.write("Fetching fastText model for OCR processing")

        needs_download = False

        if not hasattr(self.config, "model_file"):
            if _is_main_process:
                warnings.warn(
                    "'model_file' key is required but missing "
                    "from FastTextProcessor's config."
                )
            needs_download = True

        model_file = self.config.model_file
        model_file = os.path.join(get_pythia_root(), model_file)

        if not os.path.exists(model_file):
            if _is_main_process:
                warnings.warn("No model file present at {}.".format(model_file))
            needs_download = True

        if needs_download:
            if _is_main_process:
                self.writer.write("Downloading FastText bin", "info")
            model_file = self._download_model()

        synchronize()

        self._load_fasttext_model(model_file)
        self._already_downloaded = True

    def _download_model(self):
        _is_main_process = is_main_process()

        model_file_path = os.path.join(
            get_pythia_root(), ".vector_cache", "wiki.en.bin"
        )

        if not _is_main_process:
            return model_file_path

        if os.path.exists(model_file_path):
            if _is_main_process:
                self.writer.write(
                    "Vectors already present at {}.".format(model_file_path), "info"
                )
            return model_file_path

        import requests
        from pythia.common.constants import FASTTEXT_WIKI_URL
        from tqdm import tqdm

        os.makedirs(os.path.dirname(model_file_path), exist_ok=True)
        response = requests.get(FASTTEXT_WIKI_URL, stream=True)

        with open(model_file_path, "wb") as f:
            pbar = tqdm(
                total=int(response.headers["Content-Length"]) / 4096,
                miniters=50,
                disable=not _is_main_process,
            )

            idx = 0
            for data in response.iter_content(chunk_size=4096):
                if data:
                    if idx % 50 == 0:
                        pbar.update(len(data))
                    f.write(data)
                    idx += 1

            pbar.close()

        if _is_main_process:
            self.writer.write(
                "fastText bin downloaded at {}.".format(model_file_path), "info"
            )

        return model_file_path

    def _load_fasttext_model(self, model_file):
        from fasttext import load_model

        _is_main_process = is_main_process()

        if _is_main_process:
            self.writer.write("Loading fasttext model now from %s" % model_file)

        self.model = load_model(model_file)
        # String to Vector
        self.stov = WordToVectorDict(self.model)

        if _is_main_process:
            self.writer.write("Finished loading fasttext model")

    def _map_strings_to_indices(self, tokens):
        length = min(len(tokens), self.max_length)
        tokens = tokens[:length]

        output = torch.full(
            (self.max_length, self.model.get_dimension()),
            fill_value=self.PAD_INDEX,
            dtype=torch.float,
        )

        for idx, token in enumerate(tokens):
            output[idx] = torch.from_numpy(self.stov[token])

        return output

    def __call__(self, item):
        # self._try_download()
        return super().__call__(item)


@registry.register_processor("vqa_answer")
class VQAAnswerProcessor(BaseProcessor):
    """Processor for generating answer scores for answers passed using VQA
    accuracy formula. Using VocabDict class to represent answer vocabulary,
    so parameters must specify "vocab_file". "num_answers" in parameter config
    specify the max number of answers possible. Takes in dict containing
    "answers" or "answers_tokens". "answers" are preprocessed to generate
    "answers_tokens" if passed.

    Args:
        config (ConfigNode): Configuration for the processor

    Attributes:
        answer_vocab (VocabDict): Class representing answer vocabulary
    """

    DEFAULT_NUM_ANSWERS = 10

    def __init__(self, config, *args, **kwargs):
        self.writer = registry.get("writer")
        if not hasattr(config, "vocab_file"):
            raise AttributeError(
                "'vocab_file' argument required, but not "
                "present in AnswerProcessor's config"
            )

        self.answer_vocab = VocabDict(config.vocab_file, *args, **kwargs)

        self.preprocessor = None

        if hasattr(config, "preprocessor"):
            self.preprocessor = Processor(config.preprocessor)

            if self.preprocessor is None:
                raise ValueError(
                    "No processor named {} is defined.".format(config.preprocessor)
                )

        if hasattr(config, "num_answers"):
            self.num_answers = config.num_answers
        else:
            self.num_answers = self.DEFAULT_NUM_ANSWERS
            warnings.warn(
                "'num_answers' not defined in the config. "
                "Setting to default of {}".format(self.DEFAULT_NUM_ANSWERS)
            )

    def __call__(self, item):
        """Takes in dict with answers or answers_tokens, and returns back
        a dict with answers (processed), "answers_indices" which point to
        indices of the answers if present and "answers_scores" which represent
        VQA style scores for the answers.

        Args:
            item (Dict): Dict containing answers or answers_tokens

        Returns:
            Dict: Processed answers, indices and scores.

        """
        tokens = None

        if not isinstance(item, dict):
            raise TypeError("'item' passed to processor must be a dict")

        if "answer_tokens" in item:
            tokens = item["answer_tokens"]
        elif "answers" in item:
            if self.preprocessor is None:
                raise AssertionError(
                    "'preprocessor' must be defined if you "
                    "don't pass 'answer_tokens'"
                )

            tokens = [
                self.preprocessor({"text": answer})["text"]
                for answer in item["answers"]
            ]
        else:
            raise AssertionError(
                "'answers' or 'answer_tokens' must be passed"
                " to answer processor in a dict"
            )

        tokens = self._increase_to_ten(tokens)
        answers_indices = torch.zeros(self.DEFAULT_NUM_ANSWERS, dtype=torch.long)
        answers_indices.fill_(self.answer_vocab.get_unk_index())

        for idx, token in enumerate(tokens):
            answers_indices[idx] = self.answer_vocab.word2idx(token)

        answers_scores = self.compute_answers_scores(answers_indices)

        return {
            "answers": tokens,
            "answers_indices": answers_indices,
            "answers_scores": answers_scores,
        }

    def get_vocab_size(self):
        """Get vocab size of the answer vocabulary. Can also include
        soft copy dynamic answer space size.

        Returns:
            int: size of the answer vocabulary

        """
        return self.answer_vocab.num_vocab

    def get_true_vocab_size(self):
        """True vocab size can be different from normal vocab size in some cases
        such as soft copy where dynamic answer space is added.

        Returns:
            int: True vocab size.

        """
        return self.answer_vocab.num_vocab

    def word2idx(self, word):
        """Convert a word to its index according to vocabulary

        Args:
            word (str): Word to be converted to index.

        Returns:
            int: Index of the word.

        """
        return self.answer_vocab.word2idx(word)

    def idx2word(self, idx):
        """Index to word according to the vocabulary.

        Args:
            idx (int): Index to be converted to the word.

        Returns:
            str: Word corresponding to the index.

        """
        return self.answer_vocab.idx2word(idx)

    def compute_answers_scores(self, answers_indices):
        """Generate VQA based answer scores for answers_indices.

        Args:
            answers_indices (torch.LongTensor): tensor containing indices of the answers

        Returns:
            torch.FloatTensor: tensor containing scores.

        """
        scores = torch.zeros(self.get_vocab_size(), dtype=torch.float)
        gt_answers = list(enumerate(answers_indices))
        unique_answers = set(answers_indices.tolist())

        for answer in unique_answers:
            accs = []
            for gt_answer in gt_answers:
                other_answers = [item for item in gt_answers if item != gt_answer]

                matching_answers = [item for item in other_answers if item[1] == answer]
                acc = min(1, float(len(matching_answers)) / 3)
                accs.append(acc)
            avg_acc = sum(accs) / len(accs)

            if answer != self.answer_vocab.UNK_INDEX:
                scores[answer] = avg_acc

        return scores

    def _increase_to_ten(self, tokens):
        while len(tokens) < self.DEFAULT_NUM_ANSWERS:
            tokens += tokens[:self.DEFAULT_NUM_ANSWERS - len(tokens)]

        return tokens


@registry.register_processor("multi_hot_answer_from_vocab")
class MultiHotAnswerFromVocabProcessor(VQAAnswerProcessor):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

    def compute_answers_scores(self, answers_indices):
        scores = torch.zeros(self.get_vocab_size(), dtype=torch.float)
        scores[answers_indices] = 1
        scores[self.answer_vocab.UNK_INDEX] = 0
        return scores


@registry.register_processor("soft_copy_answer")
class SoftCopyAnswerProcessor(VQAAnswerProcessor):
    """Similar to Answer Processor but adds soft copy dynamic answer space to it.
    Read https://arxiv.org/abs/1904.08920 for extra information on soft copy
    and LoRRA.

    Args:
        config (ConfigNode): Configuration for soft copy processor.

    """

    DEFAULT_MAX_LENGTH = 50

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

        if hasattr(config, "max_length"):
            self.max_length = config.max_length
        else:
            self.max_length = self.DEFAULT_MAX_LENGTH
            warnings.warn(
                "'max_length' not defined in the config. "
                "Setting to default of {}".format(self.DEFAULT_MAX_LENGTH)
            )

        self.context_preprocessor = None
        if hasattr(config, "context_preprocessor"):
            self.context_preprocessor = Processor(config.context_preprocessor)

    def get_vocab_size(self):
        """Size of Vocab + Size of Dynamic soft-copy based answer space

        Returns:
            int: Size of vocab + size of dynamic soft-copy answer space.

        """
        answer_vocab_nums = self.answer_vocab.num_vocab
        answer_vocab_nums += self.max_length

        return answer_vocab_nums

    def get_true_vocab_size(self):
        """Actual vocab size which only include size of the vocabulary file.

        Returns:
            int: Actual size of vocabs.

        """
        return self.answer_vocab.num_vocab

    def __call__(self, item):
        answers = item["answers"]
        scores = super().__call__({"answers": answers})

        indices = scores["answers_indices"]
        answers = scores["answers"]
        scores = scores["answers_scores"]

        tokens_scores = scores.new_zeros(self.max_length)
        tokens = item["tokens"]
        length = min(len(tokens), self.max_length)

        gt_answers = list(enumerate(answers))
        unique_answers = set(answers)

        if self.context_preprocessor is not None:
            tokens = [
                self.context_preprocessor({"text": token})["text"] for token in tokens
            ]

        answer_counter = Counter(answers)

        for idx, token in enumerate(tokens[:length]):
            if answer_counter[token] == 0:
                continue
            accs = []

            for gt_answer in gt_answers:
                other_answers = [item for item in gt_answers if item != gt_answer]
                matching_answers = [item for item in other_answers if item[1] == token]
                acc = min(1, float(len(matching_answers)) / 3)
                accs.append(acc)

            tokens_scores[idx] = sum(accs) / len(accs)

        # Scores are already proper size, see L314. Now,
        # fix scores for soft copy candidates
        scores[-len(tokens_scores) :] = tokens_scores
        return {
            "answers": answers,
            "answers_indices": indices,
            "answers_scores": scores,
        }


@registry.register_processor("simple_word")
class SimpleWordProcessor(BaseProcessor):
    """Tokenizes a word and processes it.

    Attributes:
        tokenizer (function): Type of tokenizer to be used.

    """

    def __init__(self, *args, **kwargs):
        from pythia.utils.text_utils import word_tokenize

        self.tokenizer = word_tokenize

    def __call__(self, item, *args, **kwargs):
        return {"text": self.tokenizer(item["text"], *args, **kwargs)}


@registry.register_processor("simple_sentence")
class SimpleSentenceProcessor(BaseProcessor):
    """Tokenizes a sentence and processes it.

    Attributes:
        tokenizer (function): Type of tokenizer to be used.

    """

    def __init__(self, *args, **kwargs):
        from pythia.utils.text_utils import tokenize

        self.tokenizer = tokenize

    def __call__(self, item, *args, **kwargs):
        return {"text": self.tokenizer(item["text"], *args, **kwargs)}


@registry.register_processor("bbox")
class BBoxProcessor(VocabProcessor):
    """Generates bboxes in proper format.
    Takes in a dict which contains "info" key which is a list of dicts
    containing following for each of the the bounding box

    Example bbox input::

        {
            "info": [
                {
                    "bounding_box": {
                        "top_left_x": 100,
                        "top_left_y": 100,
                        "width": 200,
                        "height": 300
                    }
                },
                ...
            ]
        }


    This will further return a Sample in a dict with key "bbox" with last
    dimension of 4 corresponding to "xyxy". So sample will look like following:

    Example Sample::

        Sample({
            "coordinates": torch.Size(n, 4),
            "width": List[number], # size n
            "height": List[number], # size n
            "bbox_types": List[str] # size n, either xyxy or xywh.
            # currently only supports xyxy.
        })

    """

    def __init__(self, config, *args, **kwargs):
        from pythia.utils.dataset_utils import build_bbox_tensors

        self.lambda_fn = build_bbox_tensors
        self._init_extras(config)

    def __call__(self, item):
        info = item["info"]
        if self.preprocessor is not None:
            info = self.preprocessor(info)

        return {"bbox": self.lambda_fn(info, self.max_length)}


@registry.register_processor("caption")
class CaptionProcessor(BaseProcessor):
    """Processes a caption with start, end and pad tokens and returns raw string.

    Args:
        config (ConfigNode): Configuration for caption processor.

    """

    def __init__(self,  config, *args, **kwargs):
        if not hasattr(config, "vocab"):
            raise AttributeError(
                "config passed to the processor has no " "attribute vocab"
            )

        self.vocab = Vocab(*args, **config.vocab, **kwargs)

    def __call__(self, item):
        for idx, v in enumerate(item):
            if v == self.vocab.EOS_INDEX:
                item = item[:idx]
                break
        tokens = [
            self.vocab.get_itos()[w]
            for w in item
            if w
            not in {self.vocab.SOS_INDEX, self.vocab.EOS_INDEX, self.vocab.PAD_INDEX}
        ]
        caption = " ".join(tokens)
        return {"tokens": tokens, "caption": caption}


@registry.register_processor("phoc")
class PhocProcessor(VocabProcessor):
    """
    Compute PHOC features from text tokens
    """
    def __init__(self, config, *args, **kwargs):
        self._init_extras(config)
        self.config = config

    def _map_strings_to_indices(self, tokens):
        length = min(len(tokens), self.max_length)
        tokens = tokens[:length]

        phoc_dim = 604
        output = torch.full(
            (self.max_length, phoc_dim),
            fill_value=self.PAD_INDEX,
            dtype=torch.float,
        )

        for idx, token in enumerate(tokens):
            output[idx] = torch.from_numpy(build_phoc(token))

        return output


@registry.register_processor("copy")
class CopyProcessor(BaseProcessor):
    """
    Copy boxes from numpy array
    """
    def __init__(self, config, *args, **kwargs):
        self.max_length = config.max_length

    def __call__(self, item):
        blob = item["blob"]
        final_blob = np.zeros((self.max_length,) + blob.shape[1:], blob.dtype)
        final_blob[:len(blob)] = blob[:len(final_blob)]

        return {"blob": torch.from_numpy(final_blob)}


@registry.register_processor("bert_tokenizer")
class BertTokenizerProcessor(BaseProcessor):
    """
    Tokenize a text string with BERT tokenizer
    """
    def __init__(self, config, *args, **kwargs):
        self.max_length = config.max_length
        self.bert_tokenizer = BertTokenizer.from_pretrained(
            'bert-base-uncased')
        assert self.bert_tokenizer.encode(self.bert_tokenizer.pad_token) == [0]
        self.get_qgen_inds = getattr(config, 'get_qgen_inds', False)
        if self.get_qgen_inds:
            print('computing question generation indices in bert tokenizer')

    def get_vocab_size(self):
        return self.bert_tokenizer.vocab_size

    def __call__(self, item):
        # [PAD] in self.bert_tokenizer is zero (as checked in assert above)
        token_inds = torch.zeros(self.max_length, dtype=torch.long)

        indices = self.bert_tokenizer.encode(
            item['question'], add_special_tokens=True)
        indices = indices[:self.max_length]
        token_inds[:len(indices)] = torch.tensor(indices)
        token_num = torch.tensor(len(indices), dtype=torch.long)

        results = {'token_inds': token_inds, 'token_num': token_num}

        if self.get_qgen_inds:
            # default will be -1 (ignored labels in softmax loss)
            qgen_inds = -torch.ones(self.max_length, dtype=torch.long)
            # stripping [CLS] at beginning and [SEP] at end
            # then add two [PAD] at end (as stop tokens)
            indices_qgen = indices[1:-1] + [0, 0]
            qgen_inds[:len(indices_qgen)] = torch.tensor(indices_qgen)
            results['qgen_inds'] = qgen_inds

        return results


@registry.register_processor("m4c_answer")
class M4CAnswerProcessor(BaseProcessor):
    """
    Process a TextVQA answer for iterative decoding in M4C
    """
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

        self.answer_vocab = VocabDict(config.vocab_file, *args, **kwargs)
        self.PAD_IDX = self.answer_vocab.word2idx('<pad>')
        self.BOS_IDX = self.answer_vocab.word2idx('<s>')
        self.EOS_IDX = self.answer_vocab.word2idx('</s>')
        self.UNK_IDX = self.answer_vocab.UNK_INDEX

        # make sure PAD_IDX, BOS_IDX and PAD_IDX are valid (not <unk>)
        assert self.PAD_IDX != self.answer_vocab.UNK_INDEX
        assert self.BOS_IDX != self.answer_vocab.UNK_INDEX
        assert self.EOS_IDX != self.answer_vocab.UNK_INDEX
        assert self.PAD_IDX == 0

        self.answer_preprocessor = Processor(config.preprocessor)
        assert self.answer_preprocessor is not None

        self.num_answers = config.num_answers
        self.max_length = config.max_length
        self.max_copy_steps = config.max_copy_steps
        assert self.max_copy_steps >= 1

        self.match_answer_to_unk = False

    def tokenize(self, sentence):
        return sentence.split()

    def get_anls(self, s1, s2):
        s1 = s1.lower().strip()
        s2 = s2.lower().strip()
        iou = 1 - editdistance.eval(s1, s2) / max(len(s1), len(s2))
        anls = iou if iou >= .5 else 0.
        return anls

    def match_answer_to_vocab_ocr_seq(
        self, answer, vocab2idx_dict, ocr_tokens, ocr_num, ocr2inds_dict, max_match_num=20
    ):
        """
        Match an answer to a list of sequences of indices
        each index corresponds to either a fixed vocabulary or an OCR token
        (in the index address space, the OCR tokens are after the fixed vocab)
        """
        num_vocab = len(vocab2idx_dict)

        answer_words = self.tokenize(answer)
        answer_word_matches = []
        for word in answer_words:
            # match answer word to fixed vocabulary
            matched_inds = []
            if word in vocab2idx_dict:
                matched_inds.append(vocab2idx_dict.get(word))
            # match answer word to OCR
            # we put OCR after the fixed vocabulary in the answer index space
            # so add num_vocab offset to the OCR index
            matched_inds.extend(
                [num_vocab + idx for idx in ocr2inds_dict[word]]
            )
            # no exact matched ocr, find most similar one
            if len(matched_inds) == 0 and ocr_num != 0:
                ocr_scores = torch.zeros(ocr_num, dtype=torch.float)
                for i in range(ocr_num):
                    ocr_scores[i] = self.get_anls(word, ocr_tokens[i])
                most_similar_id = torch.argmax(ocr_scores).item()
                most_similar_score = self.get_anls(word, ocr_tokens[most_similar_id])
                if most_similar_score >= 0.5:
                    matched_inds.append(num_vocab + most_similar_id)
                else:
                    return []
            if len(matched_inds) == 0 and ocr_num == 0:
                if self.match_answer_to_unk:
                    matched_inds.append(vocab2idx_dict.get('<unk>'))
                else:
                    return []
            answer_word_matches.append(matched_inds)

        # expand per-word matched indices into the list of matched sequences
        if len(answer_word_matches) == 0:
            return []
        idx_seq_list = [()]
        for matched_inds in answer_word_matches:
            idx_seq_list = [
                seq + (idx,)
                for seq in idx_seq_list for idx in matched_inds
            ]
            if len(idx_seq_list) > max_match_num:
                idx_seq_list = idx_seq_list[:max_match_num]

        return idx_seq_list

    def get_vocab_size(self):
        answer_vocab_nums = self.answer_vocab.num_vocab
        answer_vocab_nums += self.max_length

        return answer_vocab_nums

    def get_true_vocab_size(self):
        return self.answer_vocab.num_vocab

    def compute_answer_scores(self, answers):
        gt_answers = list(enumerate(answers))
        unique_answers = sorted(set(answers))
        unique_answer_scores = [0] * len(unique_answers)
        for idx, unique_answer in enumerate(unique_answers):
            accs = []
            for gt_answer in gt_answers:
                other_answers = [
                    item for item in gt_answers if item != gt_answer
                ]
                matching_answers = [
                    item for item in other_answers if item[1] == unique_answer
                ]
                acc = min(1, float(len(matching_answers)) / 3)
                accs.append(acc)
            unique_answer_scores[idx] = sum(accs) / len(accs)
        unique_answer2score = {
            a: s for a, s in zip(unique_answers, unique_answer_scores)
        }
        return unique_answer2score

    def __call__(self, item):
        answers = item["answers"]
        answers = [
            self.answer_preprocessor({"text": a})["text"] for a in answers
        ]
        assert len(answers) == self.num_answers

        # Step 1: calculate the soft score of ground-truth answers
        unique_answer2score = self.compute_answer_scores(answers)

        # Step 2: fill the first step soft scores for tokens
        scores = torch.zeros(
            self.max_copy_steps,
            self.get_vocab_size(),
            dtype=torch.float
        )

        # match answers to fixed vocabularies and OCR tokens.
        ocr2inds_dict = defaultdict(list)
        for idx, token in enumerate(item["context_tokens"]):
            ocr2inds_dict[token].append(idx)
        answer_dec_inds = [
            self.match_answer_to_vocab_ocr_seq(
                a, self.answer_vocab.word2idx_dict, item["context_tokens"], item["context_length"], ocr2inds_dict
            ) for a in answers
        ]

        # Collect all the valid decoding sequences for each answer.
        # This part (idx_seq_list) was pre-computed in imdb (instead of online)
        # to save time
        all_idx_seq_list = []
        for answer, idx_seq_list in zip(answers, answer_dec_inds):
            all_idx_seq_list.extend(idx_seq_list)
            # fill in the soft score for the first decoding step
            score = unique_answer2score[answer]
            for idx_seq in idx_seq_list:
                score_idx = idx_seq[0]
                # the scores for the decoding Step 0 will be the maximum
                # among all answers starting with that vocab
                # for example:
                # if "red apple" has score 0.7 and "red flag" has score 0.8
                # the score for "red" at Step 0 will be max(0.7, 0.8) = 0.8
                scores[0, score_idx] = max(scores[0, score_idx], score)

        # train_prev_inds is the previous prediction indices in auto-regressive
        # decoding
        train_prev_inds = torch.zeros(self.max_copy_steps, dtype=torch.long)
        # train_loss_mask records the decoding steps where losses are applied
        train_loss_mask = torch.zeros(self.max_copy_steps, dtype=torch.float)
        if len(all_idx_seq_list) > 0:
            # sample a random decoding answer sequence for teacher-forcing
            idx_seq = all_idx_seq_list[np.random.choice(len(all_idx_seq_list))]
            dec_step_num = min(1+len(idx_seq), self.max_copy_steps)
            train_loss_mask[:dec_step_num] = 1.

            train_prev_inds[0] = self.BOS_IDX
            for t in range(1, dec_step_num):
                train_prev_inds[t] = idx_seq[t-1]
                score_idx = idx_seq[t] if t < len(idx_seq) else self.EOS_IDX
                scores[t, score_idx] = 1.
        else:
            idx_seq = ()

        answer_info = {
            'answers': answers,
            'answers_scores': scores,
            'sampled_idx_seq': idx_seq,
            'train_prev_inds': train_prev_inds,
            'train_loss_mask': train_loss_mask,
        }
        return answer_info


@registry.register_processor("m4c_caption")
class M4CCaptionProcessor(M4CAnswerProcessor):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        import re
        self.SENTENCE_SPLIT_REGEX = re.compile(r"(\W+)")

        self.match_answer_to_unk = True

    def tokenize(self, sentence):
        sentence = sentence.lower()
        sentence = sentence.replace(",", "").replace("?", "").replace(".", "")\
            .replace("'s", " 's")
        tokens = self.SENTENCE_SPLIT_REGEX.split(sentence)
        tokens = [t.strip() for t in tokens if len(t.strip()) > 0]
        return tokens

    def compute_answer_scores(self, answers):
        unique_answer2score = {a: 1. for a in answers}
        return unique_answer2score

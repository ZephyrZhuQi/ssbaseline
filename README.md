# Simple is not Easy: A Simple Strong Baseline for TextVQA and TextCaps

Here is the code for ssbassline model. We also provide OCR results/features/models. The code is built on top of [M4C](https://github.com/ronghanghu/mmf/tree/project/m4c_captioner_pre_release/projects/M4C), where more detailed information can also be found.

## Citation

If you use ssbaseline in your work, please cite:

```
@article{zhu2020simple,
  title={Simple is not Easy: A Simple Strong Baseline for TextVQA and TextCaps},
  author={Zhu, Qi and Gao, Chenyu and Wang, Peng and Wu, Qi},
  journal={arXiv preprint arXiv:2012.05153},
  year={2020}
}
```

## Installation

First install the repo using

```
git clone https://github.com/ZephyrZhuQi/ssbaseline.git ~/ssbaseline
cd ~/ssbaseline
python setup.py build develop
```

## Getting Data

We provide **SBD-Trans OCR** for TextVQA and ST-VQA datasets. The corresponding OCR Faster R-CNN features and Recog-CNN features are also released.

| Datasets      | ImDBs | Object Faster R-CNN Features | OCR Faster R-CNN Features | OCR Recog-CNN Features |
|--------------|-----|-------------------------------|-------------------------------|-------------------------------|
| TextVQA      | [TextVQA ImDB](https://drive.google.com/file/d/15o8WTFeGJ1LOjtCDYSL5j4dz5w7F-XpU/view?usp=sharing) | [Open Images](https://drive.google.com/file/d/1r5UDl1x1As8PI9_sDzLkuPFEyu0eL0Y7/view?usp=sharing) | [TextVQA SBD-Trans OCRs](https://drive.google.com/file/d/1t8BCTFiQBtGjUjn56Zrrydmf1ZuxSTjY/view?usp=sharing) | [TextVQA SBD-Trans OCRs](https://drive.google.com/file/d/1MdktJy8yZaL-2Z2zeVmHHo8lByNySkIX/view?usp=sharing) |
| ST-VQA      | [ST-VQA ImDB](https://drive.google.com/file/d/1UPmDNUyfIFYBXrSpdwh30hV451Hr5uN4/view?usp=sharing) | [ST-VQA Objects](https://drive.google.com/file/d/1Kh6ly1P_ru-YNpjMqQMMGLkE8AsjP0S0/view?usp=sharing) | [ST-VQA SBD-Trans OCRs](https://drive.google.com/file/d/1-vbZNHUHRs-n9dutinjvrijyXwkzbzsb/view?usp=sharing) | [ST-VQA SBD-Trans OCRs](https://drive.google.com/file/d/1rp_XvBX6INpd-ZtB_lp-4mLhcYmXWF9e/view?usp=sharing) |

## Pretrained Models

We release the following pretrained models for ssbaseline on TextVQA.

For the TextVQA dataset, we release: ssbaseline trained with ST-VQA as additional data (our best model) with SBD-Trans.

| Datasets  | Config Files (under `configs/vqa/`)         | Pretrained Models | Metrics                     | Notes                         |
|--------|------------------|----------------------------|-------------------------------|-------------------------------|
| TextVQA (`m4c_textvqa`) | `m4c_textvqa/m4c_sbd.yml`(need to modify: add data imdb and feature files of stvqa, see m4c_with_stvqa.yml for reference) | [`ssbaseline_with_stvqa`](https://drive.google.com/file/d/11ERE9szZbkiKc_NUg9VF70m_CJcHz7F0/view?usp=sharing) | val accuracy - 45.53%; test accuracy - 45.66% | SBD-Trans OCRs; ST-VQA as additional data |

## Training and Evaluation

Please follow the [M4C README](https://github.com/ronghanghu/mmf/tree/project/m4c_captioner_pre_release/projects/M4C) for the training and evaluation of the M4C model on each dataset.


## Questions and Answers from emails
Question: Feature Extraction(文章中各部分feature提取的代码有开源吗，因为要用在一些别的数据上希望可以自己提取特征)

Answer:
There are various features, and their corresponding repositories are shown below:
(各部分feature提取的代码比较多，我把我用到的给你说一下：)
1. To get the feature from OCR bounding box, you need to modify the maskrcnn detection framework by replacing the RPN layer with the hardcoded bounding box. There is a [repo](https://github.com/ronghanghu/vqa-maskrcnn-benchmark-m4c), and you should use it together with the feature extraction [script](https://github.com/facebookresearch/mmf/blob/project/m4c/projects/M4C/scripts/extract_ocr_frcn_feature.py).
1. 提取ocr bounding box中的feature，这种需要修改mask rcnn检测框架，把RPN层替换成bounding box，我使用的是这个repo中的[代码](https://github.com/ronghanghu/vqa-maskrcnn-benchmark-m4c)，需要配合提取feature的[脚本](https://github.com/facebookresearch/mmf/blob/project/m4c/projects/M4C/scripts/extract_ocr_frcn_feature.py)使用。
2. To get the feature from OBJ bounding box, you don't need modify maskrcnn framework this time, which is this [repo](https://gitlab.com/meetshah1995/vqa-maskrcnn-benchmark). The corresponding extraction [script](https://github.com/facebookresearch/mmf/blob/master/tools/scripts/features/extract_features_vmb.py).
2. 提取obj faster rcnn feature，这个不需要修改检测框架，直接提取就好，[检测框架](https://gitlab.com/meetshah1995/vqa-maskrcnn-benchmark)，[脚本](https://github.com/facebookresearch/mmf/blob/master/tools/scripts/features/extract_features_vmb.py)
3. To get the OCR bounding box, we use this [repo](https://github.com/Yuliang-Liu/Box_Discretization_Network), and the model we used is MLT 2017.
3. 获得ocr检测框的[代码](https://github.com/Yuliang-Liu/Box_Discretization_Network)，使用的模型是MLT 2017。
4. Based on the OCR bounding box, to get the OCR recognition result & extract features, the code is not mine and not opensourced yet.
4. 基于ocr检测框获得文本识别结果 & 提取ocr Recog-CNN feature，这个文本识别的代码不是我写的，也没有开源，所以目前没法分享给你




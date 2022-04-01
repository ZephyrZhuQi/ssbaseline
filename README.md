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
| TextVQA (`m4c_textvqa`) | `m4c_textvqa/m4c_sbd.yml`(need to modify: add data imdb and feature files of stvqa) | [`ssbaseline_with_stvqa`](https://drive.google.com/file/d/11ERE9szZbkiKc_NUg9VF70m_CJcHz7F0/view?usp=sharing) | val accuracy - 45.53%; test accuracy - 45.66% | SBD-Trans OCRs; ST-VQA as additional data |

## Training and Evaluation

Please follow the [M4C README](https://github.com/ronghanghu/mmf/tree/project/m4c_captioner_pre_release/projects/M4C) for the training and evaluation of the M4C model on each dataset.




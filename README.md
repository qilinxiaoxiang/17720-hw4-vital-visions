# 17-720 HW4: Vital Visions

Shawn Xiang · CMU 17-720 Machine Learning and Sensing for Healthcare, Fall 2026

- `part1/`: heart rate from a fingertip smartphone video with the torch on. `python3 part1/heart_rate.py VIDEO --out OUTDIR` writes the FFT plot, the peak plot and a results JSON.
- `part2/`: finetuning ImageNet InceptionV3 on DermaMNIST (HAM10000 / ISIC 2018 skin lesions). The notebook is executed with all outputs. [Open in Colab](https://colab.research.google.com/github/qilinxiaoxiang/17720-hw4-vital-visions/blob/main/part2/HW4_Shawn_Xiang_Part2_Finetune.ipynb).
- `part3/`: HiLight-style screen-to-camera BFSK through the alpha channel. `transmitter.html` runs on a 120 Hz display; `decode_video.py` decodes a 240 fps phone recording. See `part3/README.md`.

Requirements: Python 3.10+, numpy, scipy, matplotlib, opencv-python, ffmpeg; PyTorch, torchvision and medmnist for Part 2; Chrome and Node 20 for the Part 3 browser tests.

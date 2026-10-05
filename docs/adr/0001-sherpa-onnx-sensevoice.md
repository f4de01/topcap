# ADR 0001：转写用 sherpa-onnx 加载 SenseVoice-Small，不用 Whisper，也不用 funasr

日期：2026-10-04 · 状态：已接受

## 背景

本项目的内容全部是中文短视频，转写必须在用户本机完成（TikHub 对抖音没有任何字幕或转写接口），
而"轻量安装"是 v2 的首要目标：`pip install topcap` 一行装完，不装 torch，不装系统 ffmpeg。

候选有四个：Whisper 系（faster-whisper、whisper.cpp）、funasr 加载 SenseVoice、sherpa-onnx 加载 SenseVoice。

## 决策

用 **sherpa-onnx** 的 Python 轮子加载 **SenseVoice-Small int8** ONNX 模型，默认 CPU 推理；
检测到 CUDA 版 sherpa-onnx 轮子时自动切到 GPU。音频用 PyAV 解码成 16kHz 单声道 float32 直接喂模型。

## 理由

- **中文错误率**：FunAudioLLM 论文（arXiv 2407.04051 表 6）给出的字错误率，SenseVoice-Small 对
  Whisper-large-v3：AISHELL-1 为 2.96 对 5.14，WenetSpeech 会议场景为 7.44 对 18.87。短视频口语
  更接近后者。SenseVoice 还自带标点与繁简正规化（`use_itn`），省掉一道后处理。
- **体积**：sherpa-onnx CPU 轮子约 20 MB，模型 165 MB，不依赖 torch。funasr 路线要拉 torch
  （Windows CUDA 轮子 3.46 GB）且官方只支持到 Python 3.12；Whisper large-v3 模型 3 GB。
- **速度**：SenseVoice-Small 是非自回归模型，CPU 上约 17 倍实时，10 分钟视频约 35 秒。
  Whisper-large-v3 在 CPU 上接近实时，不可用。
- **跨平台**：sherpa-onnx 对 Windows、macOS（arm64 与 x86_64）、Linux 都发 PyPI 轮子，
  Python 3.7 到 3.14 全覆盖，Python 版本不必再钉死。

## 放弃的东西

- Whisper 在英文和中英混说上更好（LibriSpeech 1.82 对 3.15）。全英文内容不是本项目的目标。
- funasr 自带 VAD 模型做长音频切分。sherpa-onnx 路线要自己按静音切块，代码里实现了一个
  能量阈值切分器（约 40 行）。
- sherpa-onnx 的 CUDA 轮子不在 PyPI 上，GPU 加速是进阶步骤，需要用户按文档从 k2-fsa 的索引装。

## 后果

- 首次运行自动从 GitHub release 下载模型到 `~/.topcap/models/`，国内网络可能需要代理，
  `topcap doctor` 会报告模型状态并给出手动下载命令。
- 模型文件钉在 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`。同一 release 下
  日期更新的 `2025-09-09` 版**不是**原版 SenseVoice-Small，而是 ASLP-lab WSYue-ASR 的粤语微调
  （其 README 写明来源），实测普通话吞字、不出标点。升级模型前必须先用中文样本对比。
- 若将来要支持大量英文内容，可以加一个 `--engine` 后端，但在有真实需求前不预留抽象层。

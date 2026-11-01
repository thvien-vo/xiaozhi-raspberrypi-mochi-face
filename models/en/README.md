---
frameworks:
- other
license: Apache License 2.0
tasks:
- keyword-spotting
---

Custom wake word model in sherpa, trained on GigaSpeech XL (10,000 hours) data, with a model size of approximately 3.3 MB and BPE modeling units. Trained using icefall and converted to ONNX format, this repository is primarily intended for use with the sherpa-onnx inference engine.

The model architecture is Zipformer, which is essentially a very compact speech recognition model with modifications and constraints applied at the decoding stage to implement wake word spotting. It supports an unlimited number of custom wake words, though optimal performance may require tuning parameters individually.

#### Clone with HTTP
```bash
 git clone https://www.modelscope.cn/pkufool/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.git
```

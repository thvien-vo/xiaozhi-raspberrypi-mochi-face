<p align="center" class="trendshift">
  <a href="https://trendshift.io/repositories/14130" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/14130" alt="Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
</p>
<p align="center">
  <a href="https://github.com/huangjunsen0406/py-xiaozhi/releases/latest">
    <img src="https://img.shields.io/github/v/release/huangjunsen0406/py-xiaozhi?style=flat-square&logo=github&color=blue" alt="Release"/>
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"/>
  </a>
  <a href="https://github.com/huangjunsen0406/py-xiaozhi/stargazers">
    <img src="https://img.shields.io/github/stars/huangjunsen0406/py-xiaozhi?style=flat-square&logo=github" alt="Stars"/>
  </a>
  <a href="https://github.com/huangjunsen0406/py-xiaozhi/releases/latest">
    <img src="https://img.shields.io/github/downloads/huangjunsen0406/py-xiaozhi/total?style=flat-square&logo=github&color=52c41a1&maxAge=86400" alt="Download"/>
  </a>
  <a href="https://gitee.com/huang-jun-sen/py-xiaozhi">
    <img src="https://img.shields.io/badge/Gitee-FF5722?style=flat-square&logo=gitee" alt="Gitee"/>
  </a>
  <a href="https://huangjunsen0406.github.io/py-xiaozhi/guide/00_%E6%96%87%E6%A1%A3%E7%9B%AE%E5%BD%95.html">
    <img alt="Usage Docs" src="https://img.shields.io/badge/Usage%20Docs-Click%20to%20View-blue?labelColor=2d2d2d" />
  </a>
  <a href="https://atomgit.com/huangjunsen0406/py-xiaozhi">
    <img src="./assets/AtomGit.svg" alt="AtomGit" height="20"/>
  </a>
</p>

Simplified Chinese | [English](README.md)

## ❤️ Sponsors

> [Want to appear here?](mailto:95134130@qq.com)

<details open>
<summary>Click to collapse</summary>

[**GitDo.net**](https://gitdo.net/)

Thanks to GitDo.net for sponsoring this project! GitDo.net is an AI API aggregation relay platform where a single API Key can invoke mainstream models such as **Claude, Gemini, and GPT**. Direct connection, no proxy required, stable and efficient — an ideal choice for enterprise-grade AI programming and applications. **[Visit GitDo.net](https://gitdo.net/)**

---

|                                                                     |                                                                                                                                                                             |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [**Token Energy Station**](https://factory.pub/register?aff=3rXc)   | Thanks to Token Energy Station for sponsoring this project! Providing API relay services for mainstream models such as **GPT, Grok, and Claude** — stable, reliable, and affordable. **[Click to Register](https://factory.pub/register?aff=3rXc)** |
| [**Conscience AI**](https://www.openailaozu.com/register?aff=5Y7JEH5HBTXS) | Thanks to Conscience AI for sponsoring this project! Providing API relay services for mainstream models such as **GPT, Claude, and Gemini** — direct connection, stable and high-speed. **[Click to Register](https://www.openailaozu.com/register?aff=5Y7JEH5HBTXS)** |

</details>

## Project Introduction

py-xiaozhi is a lightweight, cross-platform multi-modal AI interaction master control framework based on Python's asynchronous architecture, supporting real-time voice, visual recognition, and IoT device control. It can be deployed on Windows / macOS / Linux desktops as well as ARM embedded platforms such as Raspberry Pi and RDK, interfacing downward with embodied intelligent hardware and upward with Large Language Models out of the box.

> Ported and evolved from [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32), and deeply adapted by the [D-Robotics Official Ecosystem (xiaozhi-in-rdk)](https://github.com/D-Robotics/xiaozhi-in-rdk).

> **Important Notes**
>
> - Please read the [Project Documentation](https://huangjunsen0406.github.io/py-xiaozhi/) first; startup tutorials and configuration instructions are included inside.
> - `main` holds the latest code; please reinstall pip dependencies after each update.
> - **If you have already done secondary development based on this project, please do not merge the latest code directly** — the new version architecture has been significantly refactored, and a forced merge will cause numerous conflicts. It is recommended to continue maintaining based on the old version or re-adapt by referencing the new architecture.
> - [Getting Started with Xiaozhi Client from Scratch (Video Tutorial)](https://www.bilibili.com/video/BV1dWQhYEEmq/)

## Demo

- [Bilibili Demo Video](https://www.bilibili.com/video/BV1HmPjeSED2/)

![System Interface](./documents/docs/guide/images/%E7%B3%BB%E7%BB%9F%E7%95%8C%E9%9D%A2.png)

## Features

- **AI Voice Interaction** — Voice input and recognition for a natural and fluent conversation experience
- **Visual Multi-modality** — Image recognition and processing to understand image content
- **Smart Wake-up** — Multiple wake words for hands-free activation (configurable)
- **Auto Conversation Mode** — Continuous conversation for improved interaction fluency
- **MCP Tool Ecosystem** — Music playback, camera, screenshot, application management, weather query, volume control
- **Opus Codec** — Audio encoding/decoding and real-time resampling
- **Wake Word Detection** — Offline recognition based on Sherpa-ONNX, supporting multiple wake words and Pinyin matching
- **Multiple UI Modes** — GUI (PySide6 + QML) / CLI / GPIO to adapt to different environments
- **System Tray & Global Shortcuts** — Background execution and quick operations
- **WebSocket / MQTT** — Dual-protocol communication supporting WSS encrypted transmission
- **Device Activation** — v1/v2 dual protocol with automatic verification codes and device fingerprinting
- **Cross-platform** — Windows 10+ / macOS 10.15+ / Linux

## Related Projects

- [xiaozhi-desktop](https://xiaozhi.junsen.online) — Electron desktop edition supporting AEC echo cancellation, Live2D, floating window display modes, and Windows / macOS installers

## Quick Start

**Requirements**: Python >= 3.10, microphone and speaker, stable network connection

```bash
# Clone project
git clone https://github.com/huangjunsen0406/py-xiaozhi.git
cd py-xiaozhi

# Base installation (CLI / GPIO mode)
uv sync                        # Recommended
# or: pip install -e .

# GUI mode (installs PySide6 + qasync)
uv sync --extra gui            # Recommended
# or: pip install -e '.[gui]'

# Run
python main.py                 # GUI mode (default)
python main.py --mode cli      # CLI mode
python main.py --protocol mqtt # MQTT protocol
```

## Project Structure

```
py-xiaozhi/
├── main.py                     # Main application entry point
├── src/
│   ├── bootstrap/              # Application bootstrap & dependency injection
│   ├── core/                   # Core infrastructure (event bus, state management, etc.)
│   ├── plugins/                # Plugin system (audio, UI, MCP, wake word, shortcuts)
│   ├── protocols/              # Communication protocols (WebSocket / MQTT)
│   ├── audio_codecs/           # Audio codecs
│   ├── audio_processing/       # Wake word detection
│   ├── activation/             # Device activation
│   ├── mcp/                    # MCP tool system
│   │   └── tools/              # Tool modules (music/camera/screenshot/app/weather/volume)
│   ├── ui/                     # User interface
│   │   ├── gui/                # PySide6 + QML graphical interface
│   │   ├── cli/                # Command line interface
│   │   └── gpio/               # GPIO embedded interface
│   └── utils/                  # Utility functions
├── libs/                       # Third-party native libraries (libopus / webrtc_apm)
├── models/                     # Voice wake word models
├── documents/                  # VitePress documentation site
└── pyproject.toml              # Project configuration
```

## State Flow

```
                    +----------------+
                    |                |
                    v                |
+------+ Wake/Button +------------+  |   +------------+
| IDLE | ----------> | CONNECTING | -+-> | LISTENING  |
+------+             +------------+      +------------+
   ^                                           |
   |                                           | Speech Recognition Complete
   |        +------------+                     v
   +------- |  SPEAKING  | <------------------+
    Playback +------------+
    Complete
```

## Contributing

- Please read [CONTRIBUTING.md](./CONTRIBUTING.md) first for the repository contribution guide
- For the Chinese version entry, see [CONTRIBUTING_ZH.md](./CONTRIBUTING_ZH.md)
- For detailed documentation, see the [Contribution Guide](https://huangjunsen0406.github.io/py-xiaozhi/contributing)

## Maintainer Workflow

- Categorize new submissions as `bug`, `feature`, `docs`, `refactor`, or `maintenance`
- Prioritize reviewing Pull Requests with a clear scope, explicit verification steps, and complete context
- Changes affecting behavior, configuration, or public interfaces require synchronous documentation updates
- Merge after CI passes and review feedback is resolved
- Merged changes enter releases according to the normal release workflow without promising immediate shipping

## Acknowledgments

> In no particular order

[Xiaoxia](https://github.com/78)
[zhh827](https://github.com/zhh827)
[SmartArduino-Li Honggang](https://github.com/SmartArduino)
[HonestQiao](https://github.com/HonestQiao)
[vonweller](https://github.com/vonweller)
[Sun Weigong](https://space.bilibili.com/416954647)
[isamu2025](https://github.com/isamu2025)
[Rain120](https://github.com/Rain120)
[kejily](https://github.com/kejily)
[Radio bilibili Jun](https://space.bilibili.com/119751)
[Cyber Intelligence](https://shop115087494.m.taobao.com/?refer=https%3A%2F%2Fm.tb.cn%2F)

## Project Statistics

[![Star History Chart](https://star-history.dera.page/svg?repos=huangjunsen0406/py-xiaozhi&type=Date)](https://star-history.dera.page/#huangjunsen0406/py-xiaozhi&Date)

## License

[MIT License](LICENSE)

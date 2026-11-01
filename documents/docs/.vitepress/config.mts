import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitepress";
// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "PY-XIAOZHI",
  description:
    "py-xiaozhi is a Python-based Xiaozhi voice client for learning and experiencing AI voice features without hardware.",
  base: "/py-xiaozhi/",
  locales: {
    root: {
      label: "English",
      lang: "en-US",
      link: "/",
      themeConfig: {
        langMenuLabel: "Languages",
        // https://vitepress.dev/reference/default-theme-config
        nav: [
          { text: "Home", link: "/" },
          {
            text: "Guide",
            items: [
              { text: "Documentation Directory", link: "/guide/documentation-directory" },
              { text: "System Dependencies", link: "/guide/system-dependencies" },
              { text: "Configuration Guide", link: "/guide/configuration-guide" },
              { text: "Voice Interaction Modes", link: "/guide/voice-interaction-modes" },
              { text: "Keyboard Shortcuts", link: "/guide/keyboard-shortcuts" },
              { text: "Echo Cancellation", link: "/guide/echo-cancellation" },
              { text: "Wake Word", link: "/guide/wake-word" },
              { text: "Device Activation", link: "/guide/device-activation" },
              { text: "Packaging Guide", link: "/guide/packaging-guide" },
              { text: "Development Guide", link: "/guide/development-guide" },
              { text: "Troubleshooting", link: "/guide/troubleshooting" },
              { text: "Legacy Docs", link: "/guide/old_docs/usage-guide" },
            ],
          },
          { text: "Architecture", link: "/architecture/" },
          { text: "Ecosystem", link: "/ecosystem/" },
          {
            text: "MCP",
            items: [
              { text: "MCP Guide", link: "/mcp/" },
              { text: "External Plugins", link: "/mcp/plugins" },
              { text: "Camera", link: "/mcp/camera" },
              { text: "Home Assistant (HA)", link: "/mcp/ha" },
              { text: "Music", link: "/mcp/music" },
              { text: "System", link: "/mcp/system" },
            ],
          },
          { text: "Team", link: "/about/team" },
          { text: "Contributing", link: "/contributing" },
          { text: "Sponsors", link: "/sponsors/" },
        ],

        sidebar: {
          "/about/": [],
          "/mcp/": [],
          "/sponsors/": [],
          "/contributing": [],
          "/architecture/": [],
          "/about/team": [],
        },

        socialLinks: [
          {
            icon: "github",
            link: "https://github.com/huangjunsen0406/py-xiaozhi",
          },
        ],
      },
    },
    zh: {
      label: "Simplified Chinese",
      lang: "zh-CN",
      link: "/zh/",
      themeConfig: {
        langMenuLabel: "Language",
        nav: [
          { text: "Home", link: "/zh/" },
          {
            text: "Guide",
            items: [
              { text: "Documentation Directory (Important)", link: "/zh/guide/documentation-directory" },
              { text: "System Dependencies", link: "/zh/guide/system-dependencies" },
              { text: "Configuration Guide", link: "/zh/guide/configuration-guide" },
              { text: "Voice Interaction Modes", link: "/zh/guide/voice-interaction-modes" },
              { text: "Keyboard Shortcuts", link: "/zh/guide/keyboard-shortcuts" },
              { text: "Echo Cancellation", link: "/zh/guide/echo-cancellation" },
              { text: "Wake Word", link: "/zh/guide/wake-word" },
              { text: "Device Activation", link: "/zh/guide/device-activation" },
              { text: "Packaging Guide", link: "/zh/guide/packaging-guide" },
              { text: "Development Guide", link: "/zh/guide/development-guide" },
              { text: "Troubleshooting", link: "/zh/guide/troubleshooting" },
              { text: "Legacy Docs", link: "/zh/guide/old_docs/usage-guide" },
            ],
          },
          { text: "Architecture", link: "/zh/architecture/" },
          { text: "Ecosystem", link: "/zh/ecosystem/" },
          {
            text: "MCP",
            items: [
              { text: "MCP Guide", link: "/zh/mcp/" },
              { text: "External Plugins", link: "/zh/mcp/plugins" },
              { text: "Camera", link: "/zh/mcp/camera" },
              { text: "Home Assistant (HA)", link: "/zh/mcp/ha" },
              { text: "Music", link: "/zh/mcp/music" },
              { text: "System", link: "/zh/mcp/system" },
            ],
          },
          { text: "Team", link: "/zh/about/team" },
          { text: "Contributing", link: "/zh/contributing" },
          { text: "Sponsors", link: "/zh/sponsors/" },
        ],

        sidebar: {
          "/zh/about/": [],
          "/zh/mcp/": [],
          "/zh/sponsors/": [],
          "/zh/contributing": [],
          "/zh/architecture/": [],
          "/zh/about/team": [],
        },

        socialLinks: [
          {
            icon: "github",
            link: "https://github.com/huangjunsen0406/py-xiaozhi",
          },
        ],
      },
    },
  },
  vite: {
    plugins: [tailwindcss()],
  },
});

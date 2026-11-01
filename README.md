# Notepad++ 启动优化

基于官方 v8.1.4 改进主程序启动、主题绘制和插件延迟加载。插件使用原发布版 DLL。

```text
src/notepad-plus-plus/           Notepad++ 源码，保留上游内部目录
ref/notepad-startup-analysis/    构建说明与测量脚本
tmp/<会话标识>/                 本地运行副本与输出，Git 忽略
```

在项目根目录使用 Git。主程序源码位于 `src/notepad-plus-plus/PowerEditor/src`，构建入口位于 `src/notepad-plus-plus/PowerEditor/visual.net/notepadPlus.sln`。

构建和换电脑使用的方法见 [运行说明](ref/notepad-startup-analysis/README.md)。上游说明与许可证保留在 [源码目录](src/notepad-plus-plus/README.md) 和 [LICENSE](src/notepad-plus-plus/LICENSE)。

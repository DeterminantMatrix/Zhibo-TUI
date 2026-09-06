"""ZHIBO 直播监控后端核心包。

包含轮询调度（monitor）、配置持久化（config）、导入预览、平台代理、
插件系统（plugins）、便携工具更新与 mpv 播放支持。前端界面在
``qt_quick`` 包中，通过本包提供的 MonitorService 与插件注册表驱动。
"""

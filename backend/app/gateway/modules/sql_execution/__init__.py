"""Gateway SQL Execution 垂直业务模块。

模块根保持轻量，调用方应从 ``contracts``、``router`` 或 ``service`` 具体模块导入，
避免 Gateway services 与 Router 初始化形成循环依赖。
"""

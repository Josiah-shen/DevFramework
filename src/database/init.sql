-- 文件用途：数据库初始化脚本，仅负责创建数据库实例与字符集，不涉及建表。
-- 调用时机：`make db-init`（以及 `make db-reset` 的重建链路）会通过 `mysql < init.sql` 执行本文件。
-- 执行上下文：Makefile 用不带 `-D` 的 mysql client 管道执行，因此本文件不能依赖已选中的库，须自建。
-- 扩展指引：新项目若要改库名，请同步修改 .env 中的 PROJECT_NAME 与下面 CREATE DATABASE 的目标名；
--           业务表定义一律写到 schema.sql，本文件保持"只建库"的最小职责。

CREATE DATABASE IF NOT EXISTS `xptsqas`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

-- 文件用途：业务表结构定义入口，所有 CREATE TABLE 语句写在本文件内。
-- 调用时机：`make db-schema`（以及 `make db-reset`）会通过 `mysql $PROJECT_NAME < schema.sql` 执行本文件。
-- 执行上下文：mysql client 已将目标库作为位置参数选中，本文件无需 `USE`，直接写建表语句即可。
-- 扩展指引：新项目在下方"-- 在此处定义业务表"处追加建表语句；保持幂等请统一使用
--           `CREATE TABLE IF NOT EXISTS`，列定义遵循 utf8mb4/InnoDB 约定；下方示例骨架默认注释，
--           取消注释即可作为第一张业务表的起点。

-- 在此处定义业务表

-- 示例骨架（按需取消注释并改名）：
-- CREATE TABLE IF NOT EXISTS `example_entity` (
--   `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
--   `name`       VARCHAR(128)    NOT NULL,
--   `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
--   `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
--   PRIMARY KEY (`id`),
--   KEY `idx_example_entity_name` (`name`)
-- ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

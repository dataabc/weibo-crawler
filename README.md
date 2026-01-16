# 微博爬虫 (Weibo Crawler) - 增强版

本仓库基于 [dataabc/weibo-crawler](https://github.com/dataabc/weibo-crawler) 进行二次开发。

## 🌟 新增特性：全方位时间对齐 (EXIF & 文件系统)

为了解决下载微博媒体文件（图片和视频）后，由于下载顺序导致的文件排序混乱问题，本仓库新增了时间信息回写功能：

1. **EXIF 元数据注入 (图片专用)**：
* 自动获取微博发布时间并写入图片的 **EXIF (DateTimeOriginal)** 字段。
* 导入手机相册、Google Photos 或 macOS Photos 后，图片将严格按照**微博发布时间**排列，而非下载时间。


2. **文件系统时间同步 (全格式支持)**：
* 强制修改文件在操作系统中的**修改时间 (mtime)**。
* 无论是 **视频 (MP4/MOV)**、**GIF** 还是 **PNG**，在 Windows 资源管理器或 macOS Finder 中按“日期”排序时，都能完美还原微博发布顺序。



---

## 🚀 配置说明

仓库已预设相关配置，您可以直接在 `config.json` 中灵活控制这两个功能：

### 新增配置项

| 配置项 | 参数值 | 说明 |
| --- | --- | --- |
| **`write_time_in_exif`** | `1` / `0` | **1为启用(默认)**：下载图片时将发布时间写入 EXIF。仅支持 `.jpg` 和 `.jpeg`。 |
| **`change_file_time`** | `1` / `0` | **1为启用(默认)**：修改文件的系统属性（修改日期），支持**所有**下载的文件格式。 |

### 配置示例

```json
{
    "write_time_in_exif": 1,
    "change_file_time": 1
}

```

---

## 🛠 环境要求

本项目已更新 `requirements.txt`。主要新增依赖：

* `piexif`：用于处理图片元数据写入。

---

## 📝 致谢

感谢 [dataabc/weibo-crawler](https://github.com/dataabc/weibo-crawler) 提供的核心爬取逻辑。

# 墨桥

本地离线的多格式文件转 Markdown 工具，提供 Windows 桌面界面和命令行。PDF 中能可靠识别的表格会输出为 Markdown 表格，嵌入图片和有意义的矢量图形会保存到资源目录并在正文中引用。

## 支持格式

| 输入 | 转换内容 |
| --- | --- |
| PDF | 文本、表格、嵌入图片、矢量图形区域、页面边界；低清线稿与矢量图按 300 DPI 优化导出，并自动合并重叠的图片/矢量截图 |
| XMind | 新版 `content.json`、旧版 `content.xml`、主题层级、备注、标签、链接、标记、关系、附件 |
| DOCX | 标题、段落、列表、表格、文档图片 |
| PPTX | 幻灯片顺序、文本、表格、图片 |
| XLSX | 工作表和单元格区域，输出为 Markdown 表格 |
| HTML / HTM | 标题、段落、列表、表格、本地图片、引用、代码块 |
| TXT / CSV | 文本或 Markdown 表格 |
| JSON / XML | 格式化代码块或层级列表 |

转换器由 `src/pdf2mdx/converters.py` 中的注册表管理。新增格式只需注册扩展名和转换函数，GUI、CLI 与批处理逻辑不需要改写。

## 使用

要求 Python 3.11 或更高版本。

```powershell
python -m pip install -e .
python -m pdf2mdx.gui
```

GUI 支持多文件选择和原生文件拖放、输出目录、表格/图片/矢量图形选项、PDF 密码、转换进度和结果记录。上次使用的输出目录会自动恢复；转换任务在独立进程中执行，处理大文件时界面仍可响应。所有处理均在本机完成，不连接服务端。

命令行：

```powershell
# 单文件
python -m pdf2mdx report.pdf -o output

# 多文件或整个目录
python -m pdf2mdx report.pdf map.xmind data.xlsx -o output
python -m pdf2mdx . -o output

# PDF 选项
python -m pdf2mdx report.pdf --no-images --no-vectors
python -m pdf2mdx report.pdf --no-enhance-line-art
python -m pdf2mdx encrypted.pdf --password "密码"
```

退出码：`0` 全部成功，`1` 没有找到输入文件，`2` 至少一个文件失败。

## 输出结构

```text
output/
├─ report.md
├─ report_assets/
│  ├─ report_img_001.png
│  └─ report_vec_001.png
├─ map.md
└─ map_assets/
```

## 构建 Windows EXE

先安装项目与构建依赖，再运行脚本：

```powershell
python -m pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

产物：

- `dist\墨桥.exe`：无控制台窗口的桌面程序。

桌面程序依赖 Windows WebView2 Runtime。Windows 10/11 通常已经安装；缺失时可安装微软官方 WebView2 Runtime。

## 项目结构

```text
src/pdf2mdx/
├── __init__.py          # 版本信息
├── __main__.py          # python -m pdf2mdx 入口
├── cli.py               # 命令行接口
├── converters.py        # 多格式转换注册表
├── pdf_converter.py     # PDF 转换器 (PyMuPDF)
├── xmind_converter.py   # XMind 转换器
├── gui.py               # pywebview 桌面 GUI
├── markdown_utils.py    # Markdown 工具函数
└── frontend/            # Web 前端 (HTML/CSS/JS)
```

## 测试

```powershell
python -m pytest -q
```

## 已知边界

- Markdown 本身无法保留字体、分页、浮动布局和复杂动画。
- PDF 多栏阅读顺序、无边框表格、扫描件 OCR 仍可能需要人工校正。
- 单色技术线稿默认会增强对比度，深色背景线稿会转成更易阅读和打印的黑线白底；可通过 `--no-enhance-line-art` 关闭。
- 矢量图形采用区域渲染，目标是保留可读信息，不是把图形重建为可编辑矢量对象。
- DOCX 图片当前集中列在"文档图片"章节；PPTX 图片按幻灯片位置排序。
- XLSX 面向数据区域，不保留公式计算引擎、图表和条件格式。

## License

MIT，见 [LICENSE](LICENSE)。

<p align="center">
  <img src="python/static/favicon.svg" alt="Tune Tree" width="80" height="80">
</p>

<h1 align="center">Tune Tree</h1>

<p align="center">
  <strong>自托管音乐库整理工具</strong>
</p>

<p align="center">
  扫描、浏览、重组本地音乐收藏，提供简洁的 Web 界面。<br>
  自动读取元数据、检测重复文件、批量整理、批量刮削、歌词编辑与打轴、定时任务。
</p>

<p align="center">
  <a href="#功能特性">功能特性</a> &middot;
  <a href="#快速开始">快速开始</a> &middot;
  <a href="#配置">配置</a> &middot;
  <a href="#api-一览">API 一览</a> &middot;
  <a href="#命名规则">命名规则</a>
</p>

***

## 功能特性

- **目录扫描** -- 递归扫描音乐根目录，增量更新（仅处理变更文件），自动关联艺术家和专辑信息
- **Web 浏览** -- 按艺术家、专辑、曲目浏览，单页前端界面
- **待定文件** -- 列出缺少完整元数据的文件，支持在线编辑元数据
- **元数据读取** -- 通过 mutagen 提取 ID3v2 / Vorbis Comment 标签、内嵌封面和歌词
- **元数据刮削** -- 自动从网易云音乐、酷狗、QQ音乐获取缺失的标签、歌词和封面，支持多源并行搜索与智能匹配评分，支持在文件浏览视图批量勾选刮削
- **专辑封面** -- 支持专辑封面自动提取和整理
- **定时任务** -- 支持定时自动扫描和整理音乐库，可配置执行间隔（最小5分钟），支持手动触发
- **歌词编辑** -- 支持在线编辑歌词，包括时间轴同步，支持网页加载音乐边听歌边快速编辑时间轴，支持快捷键和自动跳转，支持编辑进度缓存
- **在线播放** -- 支持在浏览器中直接播放音乐，支持 Range 请求断点续传
- **文件下载** -- 支持单曲、专辑、艺术家、批量、目录等多种下载方式（单曲直传，多曲自动打包 ZIP）
- **文件格式化** -- 预览并执行批量重命名与移动，整理为 `{艺术家}/{专辑}/` 目录结构，支持多艺术家批量操作
- **艺术家封面** -- 支持上传、刮削、删除艺术家封面图片，自动转换为 JPEG 格式
- **重复检测** -- 识别音乐库中的重复文件
- **访问控制** -- 基于 Token 的简单认证

## 平台预览

### 艺术家列表及详情

![艺术家列表及详情](https://files.seeusercontent.com/2026/06/03/ak1H/image1.png)

### 编辑元数据

![编辑元数据](https://files.seeusercontent.com/2026/06/03/6nKa/image11.png)

### 搜索元数据标签

![搜索元数据标签](https://files.seeusercontent.com/2026/06/03/Tfp2/image12.png)

### 搜索歌词、编辑歌词和时间轴

![搜索歌词、编辑歌词和时间轴](https://files.seeusercontent.com/2026/06/03/E4un/image13.png)

### 单艺术家格式化预览

![单艺术家格式化预览](https://files.seeusercontent.com/2026/06/03/5Ira/image14.png)

### 艺术家多选格式化预览

![艺术家多选格式化预览](https://files.seeusercontent.com/2026/06/03/Jeq9/image15.png)

### 目录浏览

![目录浏览](https://files.seeusercontent.com/2026/06/03/yK5w/image2.png)

### 批量获取标签

![批量获取标签](https://files.seeusercontent.com/2026/06/03/Ycf6/image21.png)

### 艺术家多选格式化

![艺术家多选格式化](https://files.seeusercontent.com/2026/06/03/Bnl8/image3.png)

### 专辑多选格式化

![专辑多选格式化](https://files.seeusercontent.com/2026/06/03/6xNn/image4.png)

### 统计概览

![统计概览](https://files.seeusercontent.com/2026/06/03/oi2B/image5.png)

### 重复文件详情

![重复文件详情](https://files.seeusercontent.com/2026/06/03/Ud9d/image6.png)

### 定时任务

![定时任务](https://files.seeusercontent.com/2026/06/04/Mgi9/image_50.png)

### 夜间模式

![夜间模式](https://files.seeusercontent.com/2026/06/03/J9me/image7.png)

## 技术栈

| 分类       | 技术                  | 版本    |
| -------- | ------------------- | ----- |
| 语言       | Python              | 3.13  |
| Web 框架   | Flask               | 3.0+  |
| 模板引擎     | Jinja2              | --    |
| 数据库      | SQLite3 (WAL mode)  | --    |
| 音频元数据    | mutagen             | 1.47+ |
| 图像处理     | Pillow              | 10.0+ |
| HTTP 客户端 | requests            | 2.31+ |
| 加密库      | cryptography        | --    |
| WSGI 服务器 | Gunicorn / Waitress | --    |
| 进程管理     | Supervisor          | --    |

## 快速开始

### 本地运行

```bash
# 1. 进入后端目录
cd python

# 2. 安装依赖
pip install -r requirements.txt

# 3. 设置音乐根目录（默认 /music，可通过环境变量覆盖）
# Windows
set MUSIC_ROOT=D:\\Music
# Linux / macOS
export MUSIC_ROOT=/path/to/your/music

# 4. 启动（开发模式）
python app.py
```

浏览器打开 <http://localhost:5000>，默认密钥 `tunetree-2026`

### 生产部署

```bash
# Linux / macOS
gunicorn wsgi:application -w 4 -b 0.0.0.0:5000

# Windows
pip install waitress
waitress-serve --host=0.0.0.0 --port=5000 wsgi:application
```

### Docker 部署

```bash
# 构建镜像
docker build -t tune-tree:1.0.0 .

# 修改 docker-compose.yml 中的卷挂载路径后启动
docker-compose up -d
```

访问 <http://localhost:15000>

## 配置

在 `python/config.py` 中修改，或通过环境变量覆盖：

| 变量           | 环境变量         | 默认值                              | 说明         |
| ------------ | ------------ | -------------------------------- | ---------- |
| `ACCESS_KEY` | `ACCESS_KEY` | `tunetree-2026`                  | 登录密钥       |
| `SECRET_KEY` | --           | `change-me-in-production-please` | Flask 会话密钥 |
| `MUSIC_ROOT` | `MUSIC_ROOT` | `/music`                         | 音乐根目录      |
| `DB_ROOT`    | `DB_ROOT`    | `instance/`                      | 数据库目录      |
| `DB_PATH`    | --           | `{DB_ROOT}/library.db`           | 数据库路径      |

> **安全提醒**：生产环境必须修改 `ACCESS_KEY` 和 `SECRET_KEY`

## 项目结构

```
tune-tree/
├── python/                    # 后端主目录
│   ├── api/                   # API 路由层
│   │   └── routes.py          # API 端点定义
│   ├── services/              # 业务逻辑层
│   │   ├── scan_service.py       # 目录扫描服务（多线程并行）
│   │   ├── format_service.py     # 文件格式化服务
│   │   ├── metadata_scraper.py   # 元数据刮削服务（多源并行+评分）
│   │   ├── similarity_service.py # 相似艺术家检测服务（自适应相似度）
│   │   ├── task_service.py       # 定时任务服务
│   │   ├── netease_api.py        # 网易云音乐 API 客户端
│   │   ├── kugou_api.py          # 酷狗音乐 API 客户端
│   │   └── qqmusic_api.py        # QQ音乐 API 客户端
│   ├── repository/            # 数据访问层
│   │   ├── artist_repository.py  # 艺术家数据操作
│   │   ├── album_repository.py   # 专辑数据操作
│   │   ├── track_repository.py   # 曲目数据操作
│   │   └── task_repository.py    # 定时任务数据操作
│   ├── models/                # 数据模型层
│   │   └── db.py              # 数据库连接与初始化（WAL 模式）
│   ├── utils/                 # 工具层
│   │   ├── metadata.py        # 元数据读写工具（含 Unicode 规范化）
│   │   ├── formatting.py      # 跨平台文件名安全处理
│   │   └── qrc_decrypt.py     # QRC 歌词解密工具（3DES + zlib）
│   ├── static/                # 静态资源
│   │   ├── css/main.css
│   │   ├── js/app.js          # 前端入口
│   │   └── js/modules/        # 前端模块
│   │       ├── api.js            # API 请求封装
│   │       ├── auth.js           # 认证模块
│   │       ├── artist.js         # 艺术家视图
│   │       ├── artist-stats.js   # 艺术家统计面板
│   │       ├── batch-scrape.js         # 批量刮削
│   │       ├── detail.js         # 曲目详情
│   │       ├── files.js          # 目录浏览
│   │       ├── format.js         # 格式化操作
│   │       ├── image-viewer.js   # 图片查看器
│   │       ├── logs.js           # 操作日志
│   │       ├── lrc-parser.js     # LRC 歌词解析
│   │       ├── lyrics-editor.js  # 歌词编辑器（含打轴）
│   │       ├── metadata-edit.js  # 元数据编辑
│   │       ├── pending.js        # 待定文件
│   │       ├── settings.js       # 设置页面
│   │       ├── state.js          # 全局状态管理
│   │       ├── stats.js          # 统计概览
│   │       ├── ui.js             # UI 组件
│   │       └── utils.js          # 工具函数
│   ├── templates/
│   │   └── index.html         # 单页前端
│   ├── instance/              # 应用数据
│   │   ├── library.db         # SQLite 数据库
│   │   └── tunetree.log       # 操作日志（按日轮转，保留30天）
│   ├── app.py                 # Flask 应用入口
│   ├── config.py              # 配置文件
│   ├── requirements.txt
│   └── wsgi.py                # Gunicorn / Waitress 入口
├── conf/                      # 配置文件
│   └── supervisord.conf       # Supervisor 进程管理配置
├── screenshots/               # 平台预览截图
├── Dockerfile                 # Docker 构建文件
├── docker-compose.yml         # Docker Compose 配置
└── LICENSE                    # GPLv3
```

## API 一览

所有接口需要 `X-Token` 请求头（值为 ACCESS\_KEY），部分接口也支持 `?token=` 查询参数。

### 认证

| 方法   | 路径                 | 说明       |
| ---- | ------------------ | -------- |
| POST | `/api/auth/verify` | 验证 token |

### 扫描

| 方法   | 路径                 | 说明     |
| ---- | ------------------ | ------ |
| POST | `/api/scan`        | 扫描音乐目录 |
| GET  | `/api/scan/status` | 查询扫描状态 |

### 艺术家与专辑

| 方法  | 路径                                    | 说明                   |
| --- | ------------------------------------- | -------------------- |
| GET | `/api/artists`                        | 获取艺术家列表（支持 `?q=` 搜索） |
| GET | `/api/artists/<int:artist_id>/albums` | 获取专辑列表               |
| GET | `/api/albums/<int:album_id>/tracks`   | 获取曲目列表               |
| GET | `/api/artists/<int:artist_id>/full`   | 获取艺术家完整信息（含所有专辑及曲目）  |

### 艺术家封面

| 方法     | 路径                                          | 说明          |
| ------ | ------------------------------------------- | ----------- |
| GET    | `/api/artists/<int:artist_id>/cover`        | 获取艺术家封面     |
| POST   | `/api/artists/<int:artist_id>/cover`        | 上传艺术家封面     |
| DELETE | `/api/artists/<int:artist_id>/cover`        | 删除艺术家封面     |
| GET    | `/api/artists/<int:artist_id>/cover/exists` | 检查艺术家封面是否存在 |
| POST   | `/api/artists/<int:artist_id>/scrape-cover` | 从网易云刮削艺术家头像 |

### 专辑封面

| 方法   | 路径                                        | 说明         |
| ---- | ----------------------------------------- | ---------- |
| GET  | `/api/albums/<int:album_id>/cover`        | 获取专辑封面     |
| POST | `/api/albums/<int:album_id>/cover`        | 上传专辑封面     |
| GET  | `/api/albums/<int:album_id>/cover/exists` | 检查专辑封面是否存在 |

### 曲目

| 方法     | 路径                                      | 说明                                                                          |
| ------ | --------------------------------------- | --------------------------------------------------------------------------- |
| GET    | `/api/tracks/<int:track_id>`            | 获取单曲详情（含歌词）                                                                 |
| DELETE | `/api/tracks/<int:track_id>`            | 删除单曲（同时删除本地文件）                                                              |
| PUT    | `/api/tracks/<int:track_id>/metadata`   | 更新曲目元数据标签                                                                   |
| PUT    | `/api/tracks/<int:track_id>/cover`      | 上传曲目封面                                                                      |
| PUT    | `/api/tracks/<int:track_id>/lyrics`     | 更新曲目歌词                                                                      |
| POST   | `/api/tracks/<int:track_id>/export-lrc` | 导出歌词为 .lrc 文件                                                               |
| GET    | `/api/tracks/<int:track_id>/audio`      | 在线播放（支持 Range）                                                              |
| GET    | `/api/tracks/<int:track_id>/download`   | 下载单曲                                                                        |
| GET    | `/api/tracks/by-path?path=`             | 按路径查找曲目                                                                     |
| POST   | `/api/tracks/batch-delete`              | 批量删除曲目（`{ track_ids: [] }`）                                                 |
| POST   | `/api/tracks/download-batch`            | 批量下载曲目（`{ track_ids: [] }`）                                                 |
| POST   | `/api/tracks/batch-scrape`              | 批量刮削元数据（`{ track_ids: [], user_inputs: { id: { title, artist, album } } }`） |

### 元数据刮削

| 方法   | 路径                                        | 说明                                                       |
| ---- | ----------------------------------------- | -------------------------------------------------------- |
| POST | `/api/tracks/<int:track_id>/scrape`       | 刮削元数据（支持指定 API）                                          |
| POST | `/api/tracks/<int:track_id>/apply-scrape` | 应用刮削结果                                                   |
| POST | `/api/tracks/<int:track_id>/scrape-all`   | 批量搜索所有 API（支持 `exclude_ids`、`title`、`artist`、`album` 参数） |

### 歌词搜索

| 方法   | 路径                      | 说明        |
| ---- | ----------------------- | --------- |
| POST | `/api/lyrics/search`    | 搜索歌词      |
| GET  | `/api/lyrics/{song_id}` | 获取歌词（含翻译） |

### 封面

| 方法  | 路径                          | 说明     |
| --- | --------------------------- | ------ |
| GET | `/api/cover/<int:track_id>` | 获取专辑封面 |

### 下载

| 方法  | 路径                                      | 说明             |
| --- | --------------------------------------- | -------------- |
| GET | `/api/artists/<int:artist_id>/download` | 下载艺术家所有曲目（ZIP） |
| GET | `/api/albums/<int:album_id>/download`   | 下载专辑所有曲目（ZIP）  |
| GET | `/api/files/download?path=`             | 下载文件或目录（ZIP）   |

### 文件浏览

| 方法  | 路径                       | 说明                                                           |
| --- | ------------------------ | ------------------------------------------------------------ |
| GET | `/api/files?path=`       | 目录浏览（支持 `limit`、`offset`、`sort`、`search`、`folders_first` 参数） |
| GET | `/api/files/audio-count` | 获取音频文件统计                                                     |

### 格式化

| 方法   | 路径                          | 说明      |
| ---- | --------------------------- | ------- |
| POST | `/api/format/preview`       | 格式化预览   |
| POST | `/api/format/execute`       | 执行格式化   |
| POST | `/api/format/batch-preview` | 批量格式化预览 |
| POST | `/api/format/batch-execute` | 批量执行格式化 |

### 统计与日志

| 方法     | 路径                | 说明   |
| ------ | ----------------- | ---- |
| GET    | `/api/stats`      | 统计数据 |
| GET    | `/api/stats/artists` | 艺术家统计数据 |
| GET    | `/api/stats/similar-artists` | 相似艺术家检测结果 |
| GET    | `/api/stats/similar-artists/<int:artist_a_id>/<int:artist_b_id>` | 相似艺术家详情对比 |
| POST   | `/api/artists/batch-scrape-covers` | 批量刮削艺术家封面 |
| GET    | `/api/pending`    | 待定文件 |
| GET    | `/api/duplicates` | 重复文件 |
| GET    | `/api/logs`       | 操作日志 |
| DELETE | `/api/logs`       | 清空日志 |

### 定时任务

| 方法   | 路径                  | 说明                                                               |
| ---- | ------------------- | ---------------------------------------------------------------- |
| GET  | `/api/task/config`  | 获取任务配置                                                           |
| POST | `/api/task/config`  | 设置任务配置（`{ scrape_enabled, organize_enabled, interval_minutes }`） |
| GET  | `/api/task/status`  | 获取任务状态（刮削、整理、定时任务）                                               |
| POST | `/api/task/execute` | 手动执行任务（`{ task_type: "scrape" \| "organize" \| "both" }`）        |
| GET  | `/api/task/running` | 检查是否有任务正在运行                                                      |

## 命名规则

格式化后文件名：`曲序号. 曲目名[ - feat. 客串].扩展名`

示例：

```
{MUSIC_ROOT}
├── Michael Jackson
│   └── HIStory_ Past, Present and Future, Book I
│       └── 05. Earth Song.flac
└── Babyface_Kenny G
    └── Love Ballads
        └── 04. Every Time I Close My Eyes.flac
```

目录结构：`{MUSIC_ROOT}/{艺术家}/{专辑}/`

## 支持格式

| 格式      | 标签标准           | 封面   | 歌词   |
| ------- | -------------- | ---- | ---- |
| `.mp3`  | ID3v2          | APIC | USLT |
| `.flac` | Vorbis Comment | 内嵌   | 歌词字段 |

## 数据模型与关联逻辑

### 数据表结构

系统使用三张核心表存储音乐数据：

- **artists**：艺术家表（id, name, dir\_name）
- **albums**：专辑表（id, title, dir\_name, artist\_id, cover\_path, year）
- **tracks**：曲目表（id, title, artist, album, artist\_id, album\_id, path, ...）

### 元数据更新关联逻辑

当更新曲目元数据（艺术家、专辑）时，系统自动处理关联关系：

```
更新元数据
├─ 仅专辑名变化
│  ├─ 新专辑存在？→ 直接更新 album_id
│  └─ 新专辑不存在 → 创建专辑 → 更新 album_id
│     └─ 旧专辑无曲目 → 删除专辑记录
│
├─ 仅艺术家变化
│  ├─ 新艺术家存在？
│  │  ├─ 新艺术家有同名专辑？→ 直接更新 artist_id, album_id
│  │  └─ 新艺术家无同名专辑 → 创建专辑 → 更新 artist_id, album_id
│  └─ 新艺术家不存在 → 创建艺术家 → 创建专辑 → 更新 artist_id, album_id
│     └─ 清理旧数据
│        ├─ 旧艺术家+旧专辑无曲目 → 删除专辑记录
│        └─ 旧艺术家无专辑 → 删除艺术家记录
│
└─ 艺术家和专辑都变化
   ├─ 新艺术家存在？
   │  ├─ 新艺术家有新专辑？→ 直接更新 artist_id, album_id
   │  └─ 新艺术家无新专辑 → 创建专辑 → 更新 artist_id, album_id
   └─ 新艺术家不存在 → 创建艺术家 → 创建专辑 → 更新 artist_id, album_id
      └─ 清理旧数据
         ├─ 旧艺术家+旧专辑无曲目 → 删除专辑记录
         └─ 旧艺术家无专辑 → 删除艺术家记录
```

**清理规则**：

- 专辑无曲目时自动删除
- 艺术家无专辑时自动删除

## Windows / Linux 特殊字符处理

支持跨平台处理特殊字符限制。

### 不允许的字符

以下字符会被自动替换为下划线 `_`：

| 字符  | Windows | Linux | 说明         | <br /> |
| --- | ------- | ----- | ---------- | :----- |
| `\` | ❌       | ✅     | 反斜杠（目录分隔符） | <br /> |
| `/` | ❌       | ❌     | 正斜杠（目录分隔符） | <br /> |
| `:` | ❌       | ✅     | 冒号         | <br /> |
| `*` | ❌       | ✅     | 星号（通配符）    | <br /> |
| `?` | ❌       | ✅     | 问号（通配符）    | <br /> |
| `"` | ❌       | ✅     | 双引号        | <br /> |
| `<` | ❌       | ✅     | 小于号        | <br /> |
| `>` | ❌       | ✅     | 大于号        | <br /> |
| \`  | \`      | ❌     | ✅          | 竖线     |

### 其他处理规则

1. **控制字符**：ASCII `\x00-\x1f` 和 `\x7f` 会被移除
2. **不可见 Unicode 字符**：零宽空格、BOM、全角空格等不可见字符会被移除
3. **Unicode 规范化**：使用 NFKC 规范化处理日文假名等字符的不同表示形式
4. **首字符处理**：
   - 以 `-` 开头会被替换为 `_`（避免被误认为命令行选项）
5. **尾字符处理**：
   - 以 `.` 结尾会被替换为 `_`（Windows 不允许目录名以点号结尾）
6. **保留名称**：
   - Windows 保留名称（`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`）会自动添加下划线后缀
7. **空名称处理**：
   - 如果处理后名称为空或只有特殊字符，会使用 `Unknown` 作为默认名称

### 示例

```
原始名称                    → 处理后名称
------------------------   → ------------------------
Michael: Jackson           → Michael_Jackson
Album / Best Of            → Album _ Best Of
"Classic" Hits             → _Classic_ Hits
-rock ballads              → _rock ballads
README.                    → README_
CON                        → CON_
aux                        → aux_
```

### 代码实现

特殊字符处理逻辑位于 `python/utils/formatting.py`：

```python
def safe_dirname(name: str) -> str:
    # 1. 替换跨平台非法字符（Windows: \/:*?"<>|，Linux: /）
    result = re.sub(r'[\\/:*?"<>|]', "_", name)
    
    # 2. 移除控制字符（ASCII 0-31 和 127）
    result = re.sub(r"[\x00-\x1f\x7f]", "", result)
    
    # 3. 去除首尾空格
    result = result.strip()
    
    # 4. 处理 Linux 特殊开头字符（以 - 开头）
    if result.startswith("-"):
        result = "_" + result[1:]
    
    # 5. 将结尾的点号替换为下划线
    result = re.sub(r"\.+$", "_", result)
    
    # 6. 检查 Windows 保留名称
    windows_reserved = {
        "con", "prn", "aux", "nul",
        "com1-9", "lpt1-9"
    }
    if result.lower() in windows_reserved:
        result = result + "_"
    
    # 7. 如果处理后为空或只有特殊字符，返回 Unknown
    return result or "Unknown"
```

## 许可证

[GPLv3](LICENSE)

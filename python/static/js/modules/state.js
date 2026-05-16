/**
 * state.js — 应用全局可变状态
 * 所有模块通过直接读写这些变量共享状态（单页应用简单模式）。
 * 若后续需要响应式，可在此基础上加 Proxy/EventEmitter 包装。
 */

/** 当前登录 Token */
let TOKEN = '';

/** 当前选中艺术家对象 { artist, album_count, track_count, all_organized } */
let currentArtist = null;

/** 当前选中专辑名称 */
let currentAlbum = null;

/** 当前艺术家的专辑列表 */
let artistAlbums = [];

/** 已勾选的专辑名称集合 */
let selectedAlbums = new Set();

/** 已勾选的曲目 id 集合 */
let selectedTracks = new Set();

/** 当前激活的页面名称 */
let currentPage = 'artist';

/** 当前主题：'light' | 'dark' */
let theme = localStorage.getItem('tt-theme') || 'light';

/** 文件浏览器排序方式：'name' | 'date' */
let fileSort = 'name';

/** 文件浏览器当前路径 */
let filePath = '';

/** 完整艺术家列表（用于搜索过滤） */
let allArtists = [];

/** 文件列表缓存 { path: { items, timestamp } } */
let filesCache = {};

/** 缓存有效期（毫秒） */
const FILES_CACHE_TTL = 5 * 60 * 1000;

/** 当前目录的原始文件列表（用于搜索过滤） */
let currentFiles = [];

/** 是否正在扫描 */
let isScanning = false;

/** 扫描开始时间戳 */
let scanStartTime = null;

/** 扫描是否超时 */
let scanTimedOut = false;

/**
 * image-viewer.js — 通用图片查看器组件
 * 功能：全屏展示、缩放、下载
 * 调用方式：showImageViewer(url, options)
 */

let currentScale = 1;
let isDragging = false;
let startX = 0;
let startY = 0;
let translateX = 0;
let translateY = 0;
let isAnimating = false;

/* ═══════════════════════════════════════════════════════════
   HTML 结构
═══════════════════════════════════════════════════════════ */

function createImageViewerHTML() {
  return `
    <div class="image-viewer-overlay" id="image-viewer">
      <div class="image-viewer-toolbar">
        <button class="image-viewer-btn" id="img-zoom-out" title="缩小">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/>
            <line x1="8" y1="11" x2="14" y2="11"/>
          </svg>
        </button>
        <span class="image-viewer-scale" id="img-scale-display">100%</span>
        <button class="image-viewer-btn" id="img-zoom-in" title="放大">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/>
            <line x1="11" y1="8" x2="11" y2="14"/>
            <line x1="8" y1="11" x2="14" y2="11"/>
          </svg>
        </button>
        <button class="image-viewer-btn" id="img-reset" title="重置">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
            <path d="M3 3v5h5"/>
          </svg>
        </button>
        <button class="image-viewer-btn" id="img-download" title="下载">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
        <button class="image-viewer-btn" id="img-close" title="关闭 (Esc)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="image-viewer-content" id="image-viewer-content">
        <img class="image-viewer-img" id="image-viewer-img" draggable="false"/>
      </div>
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════════
   INIT / DESTROY
═══════════════════════════════════════════════════════════ */

function initImageViewer() {
  if (document.getElementById('image-viewer')) return;

  const overlay = document.createElement('div');
  overlay.innerHTML = createImageViewerHTML();
  document.body.appendChild(overlay.firstElementChild);

  setupImageViewerEvents();
}

function destroyImageViewer() {
  const viewer = document.getElementById('image-viewer');
  if (viewer) {
    viewer.remove();
  }

  currentScale = 1;
  translateX = 0;
  translateY = 0;
  isDragging = false;
  isAnimating = false;
}

/* ═══════════════════════════════════════════════════════════
   EVENT SETUP
═══════════════════════════════════════════════════════════ */

function setupImageViewerEvents() {
  const viewer = document.getElementById('image-viewer');
  const content = document.getElementById('image-viewer-content');
  const img = document.getElementById('image-viewer-img');

  // Toolbar buttons
  document.getElementById('img-zoom-in').onclick = () => zoomImage(0.25);
  document.getElementById('img-zoom-out').onclick = () => zoomImage(-0.25);
  document.getElementById('img-reset').onclick = resetImage;
  document.getElementById('img-close').onclick = closeImageViewer;
  document.getElementById('img-download').onclick = downloadImage;

  // Close on overlay click
  viewer.onclick = (e) => {
    if (e.target === viewer || e.target === content) {
      closeImageViewer();
    }
  };

  // Keyboard shortcuts
  document.addEventListener('keydown', handleImageViewerKeydown);

  // Mouse drag
  content.onmousedown = (e) => {
    e.preventDefault();
    isDragging = true;
    startX = e.clientX - translateX;
    startY = e.clientY - translateY;
    content.style.cursor = 'grabbing';
  };

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    e.preventDefault();
    translateX = e.clientX - startX;
    translateY = e.clientY - startY;
    updateImageTransform();
  });

  document.addEventListener('mouseup', () => {
    isDragging = false;
    content.style.cursor = 'grab';
  });

  // Wheel zoom
  content.onwheel = (e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    zoomImage(delta);
  };

  // Touch support
  let lastTouchDistance = 0;
  let lastScale = 1;

  content.ontouchstart = (e) => {
    if (e.touches.length === 1) {
      isDragging = true;
      startX = e.touches[0].clientX - translateX;
      startY = e.touches[0].clientY - translateY;
    } else if (e.touches.length === 2) {
      isDragging = false;
      lastScale = currentScale;
      lastTouchDistance = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  };

  content.ontouchmove = (e) => {
    e.preventDefault();
    if (e.touches.length === 1 && isDragging) {
      translateX = e.touches[0].clientX - startX;
      translateY = e.touches[0].clientY - startY;
      updateImageTransform();
    } else if (e.touches.length === 2) {
      const distance = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const scale = lastScale * (distance / lastTouchDistance);
      currentScale = Math.max(0.25, Math.min(4, scale));
      updateImageTransform();
    }
  };

  content.ontouchend = () => {
    isDragging = false;
  };
}

function handleImageViewerKeydown(e) {
  if (!document.getElementById('image-viewer')) return;

  switch (e.key) {
    case 'Escape':
      closeImageViewer();
      break;
    case '+':
    case '=':
      zoomImage(0.25);
      break;
    case '-':
      zoomImage(-0.25);
      break;
    case '0':
      resetImage();
      break;
    case 'ArrowLeft':
      translateX += 20;
      updateImageTransform();
      break;
    case 'ArrowRight':
      translateX -= 20;
      updateImageTransform();
      break;
    case 'ArrowUp':
      translateY += 20;
      updateImageTransform();
      break;
    case 'ArrowDown':
      translateY -= 20;
      updateImageTransform();
      break;
  }
}

/* ═══════════════════════════════════════════════════════════
   IMAGE OPERATIONS
═══════════════════════════════════════════════════════════ */

function zoomImage(delta) {
  const img = document.getElementById('image-viewer-img');
  if (!img || isAnimating) return;

  isAnimating = true;
  currentScale = Math.max(0.25, Math.min(4, currentScale + delta));

  updateImageTransform();
  updateScaleDisplay();

  setTimeout(() => {
    isAnimating = false;
  }, 150);
}

function resetImage() {
  currentScale = 1;
  translateX = 0;
  translateY = 0;

  updateImageTransform();
  updateScaleDisplay();
}

function updateImageTransform() {
  const img = document.getElementById('image-viewer-img');
  if (!img) return;

  img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${currentScale})`;
}

function updateScaleDisplay() {
  const display = document.getElementById('img-scale-display');
  if (display) {
    display.textContent = Math.round(currentScale * 100) + '%';
  }
}

async function downloadImage() {
  const img = document.getElementById('image-viewer-img');
  if (!img || !img.src) return;

  try {
    const response = await fetch(img.src);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = 'image-' + Date.now() + '.' + getExtensionFromUrl(img.src);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    if (window.showToast) {
      showToast('图片已下载', 'success');
    }
  } catch (e) {
    // Fallback: open in new tab
    window.open(img.src, '_blank');
    if (window.showToast) {
      showToast('已在新标签页打开', 'info');
    }
  }
}

function getExtensionFromUrl(url) {
  const match = url.match(/\.(\w+)\?/);
  return match ? match[1] : 'jpg';
}

/* ═══════════════════════════════════════════════════════════
   PUBLIC API
═══════════════════════════════════════════════════════════ */

/**
 * 计算图片自适应屏幕的缩放比例
 * @param {HTMLImageElement} img - 图片元素
 * @returns {number} - 合适的缩放比例
 */
function calculateFitScale(img) {
  // 获取可用空间（减去工具栏高度和边距）
  const toolbarHeight = 56; // 工具栏高度
  const maxWidth = window.innerWidth * 0.95; // 95% 屏幕宽度
  const maxHeight = (window.innerHeight - toolbarHeight) * 0.95; // 95% 可用高度

  // 计算宽高缩放比例
  const scaleX = maxWidth / img.naturalWidth;
  const scaleY = maxHeight / img.naturalHeight;

  // 取较小值确保图片完全显示
  return Math.min(scaleX, scaleY, 1); // 最大不超过原始尺寸
}

/**
 * 显示图片查看器
 * @param {string} imageUrl - 图片 URL
 * @param {object} options - 配置选项
 * @param {string} options.filename - 文件名（用于下载）
 * @param {number} options.initialScale - 初始缩放比例（默认自适应）
 */
function showImageViewer(imageUrl, options = {}) {
  const {
    filename = 'image',
    initialScale = null // null 表示自动计算
  } = options;

  initImageViewer();

  const viewer = document.getElementById('image-viewer');
  const img = document.getElementById('image-viewer-img');
  const content = document.getElementById('image-viewer-content');

  // Reset state
  currentScale = 1;
  translateX = 0;
  translateY = 0;
  isDragging = false;
  isAnimating = false;

  // Set image source
  img.onload = () => {
    // 计算自适应缩放比例
    if (initialScale === null) {
      currentScale = calculateFitScale(img);
    } else {
      currentScale = initialScale;
    }

    img.style.opacity = '1';
    updateImageTransform();
    updateScaleDisplay();
    content.style.cursor = 'grab';
  };

  img.onerror = () => {
    if (window.showToast) {
      showToast('图片加载失败', 'error');
    }
    closeImageViewer();
  };

  img.src = imageUrl;
  img.style.opacity = '0';

  // Show overlay
  viewer.classList.add('open');
  document.body.style.overflow = 'hidden';
}

/**
 * 关闭图片查看器
 */
function closeImageViewer() {
  const viewer = document.getElementById('image-viewer');
  if (!viewer) return;

  viewer.classList.remove('open');
  document.body.style.overflow = '';

  // Clear image after fade out
  setTimeout(() => {
    const img = document.getElementById('image-viewer-img');
    if (img) {
      // 先移除 onerror 处理程序，防止设置 src='' 时触发无限循环
      img.onerror = null;
      img.src = '';
      img.style.opacity = '0';
    }
  }, 200);
}

/* ═══════════════════════════════════════════════════════════
   EXPORT
═══════════════════════════════════════════════════════════ */

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { showImageViewer, closeImageViewer };
}

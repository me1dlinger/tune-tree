
/**
 * lrc-parser.js - LRC歌词解析与序列化模块
 * 支持：时间轴+翻译、时间轴无翻译、纯文本三种格式
 */

const LrcParser = {
    TAG_REGEX: /^\[([a-z]+):(.*)\]$/i,
    TIME_REGEX: /^\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)$/,

    parse(rawText) {
        if (!rawText || !rawText.trim()) {
            return { metadata: [], groups: [], hasTimestamp: false };
        }

        const lines = rawText.split('\n');
        const metadata = [];
        const timedLines = [];
        let hasAnyTimestamp = false;

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;

            const tagMatch = trimmed.match(this.TAG_REGEX);
            if (tagMatch && !/^\d{2}:\d{2}/.test(tagMatch[1])) {
                metadata.push({ key: tagMatch[1].trim(), value: tagMatch[2].trim() });
                continue;
            }

            const timeMatch = trimmed.match(this.TIME_REGEX);
            if (timeMatch) {
                hasAnyTimestamp = true;
                const min = parseInt(timeMatch[1], 10);
                const sec = parseInt(timeMatch[2], 10);
                let ms = parseInt(timeMatch[3], 10);
                if (timeMatch[3].length === 2) ms *= 10;
                const timestamp = min * 60 + sec + ms / 1000;
                const text = timeMatch[4].trim();
                timedLines.push({ timestamp, text, timestampStr: this.formatTime(timestamp) });
            } else {
                timedLines.push({ timestamp: null, text: trimmed, timestampStr: '' });
            }
        }

        const groups = this._groupLines(timedLines);

        return { metadata, groups, hasTimestamp: hasAnyTimestamp };
    },

    _groupLines(timedLines) {
        const groups = [];
        let i = 0;

        while (i < timedLines.length) {
            const current = timedLines[i];
            const next = timedLines[i + 1];

            if (current.timestamp !== null && next && next.timestamp !== null &&
                Math.abs(current.timestamp - next.timestamp) < 0.001) {
                groups.push({
                    timestamp: current.timestamp,
                    timestampStr: current.timestampStr,
                    primary: { text: current.text, role: 'primary' },
                    secondary: { text: next.text, role: 'secondary' }
                });
                i += 2;
            } else {
                groups.push({
                    timestamp: current.timestamp,
                    timestampStr: current.timestampStr,
                    primary: { text: current.text, role: 'primary' },
                    secondary: null
                });
                i += 1;
            }
        }

        return groups;
    },

    serialize(data) {
        const parts = [];

        for (const tag of data.metadata) {
            parts.push(`[${tag.key}: ${tag.value}]`);
        }

        for (const group of data.groups) {
            if (group.timestamp !== null) {
                const ts = this.formatTime(group.timestamp);
                parts.push(`[${ts}]${group.primary.text}`);
                if (group.secondary) {
                    parts.push(`[${ts}]${group.secondary.text}`);
                }
            } else {
                parts.push(`[${group.primary.text}]`);
                if (group.secondary) {
                    parts.push(`[${group.secondary.text}]`);
                }
            }
        }

        return parts.join('\n');
    },

    formatTime(seconds) {
        if (seconds === null || seconds === undefined) return '';
        const min = Math.floor(seconds / 60);
        const sec = Math.floor(seconds % 60);
        const ms = Math.round((seconds % 1) * 1000);
        return `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
    },

    parseTimeStr(str) {
        if (!str) return null;
        const m = str.match(/^(\d{2}):(\d{2})\.(\d{2,3})$/);
        if (!m) return null;
        const min = parseInt(m[1], 10);
        const sec = parseInt(m[2], 10);
        let ms = parseInt(m[3], 10);
        if (m[3].length === 2) ms *= 10;
        return min * 60 + sec + ms / 1000;
    },

    addOffset(timestamp, offsetSeconds) {
        if (timestamp === null) return null;
        return Math.max(0, timestamp + offsetSeconds);
    }
};

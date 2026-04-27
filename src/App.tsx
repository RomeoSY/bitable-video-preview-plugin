import { bitable, type IOpenAttachment, type Selection } from '@lark-base-open/js-sdk';
import { useEffect, useMemo, useRef, useState } from 'react';

type VideoItem = {
    token: string;
    name: string;
    type: string;
    url: string;
    size: number;
    source: 'attachment' | 'url';
};

type SelectionState = Pick<Selection, 'tableId' | 'recordId' | 'fieldId'>;

const VIDEO_EXTENSIONS = new Set([
    'mp4',
    'mov',
    'm4v',
    'avi',
    'wmv',
    'webm',
    'mkv',
    'flv',
    '3gp',
    'mpeg',
    'mpg',
    'm3u8',
]);

function isAttachmentArray(value: unknown): value is IOpenAttachment[] {
    return Array.isArray(value) && value.every((item) => !!item && typeof item === 'object' && 'token' in item);
}

function isVideoAttachment(file: IOpenAttachment): boolean {
    const mime = file.type?.toLowerCase() ?? '';
    if (mime.startsWith('video/')) {
        return true;
    }

    const ext = file.name.split('.').pop()?.toLowerCase();
    return ext ? VIDEO_EXTENSIONS.has(ext) : false;
}

function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) {
        return '0 B';
    }

    const units = ['B', 'KB', 'MB', 'GB'];
    const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, idx);
    return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function extractCandidateUrls(input: unknown): string[] {
    const result = new Set<string>();
    const seen = new Set<unknown>();
    const urlPattern = /https?:\/\/[^\s"'<>]+/gi;

    const normalizeUrl = (raw: string): string => {
        return raw
            .replace(/&amp;/gi, '&')
            .replace(/\\u0026/gi, '&')
            .replace(/\\x26/gi, '&')
            .trim();
    };

    const walk = (node: unknown): void => {
        if (!node || seen.has(node)) {
            return;
        }
        if (typeof node === 'string') {
            const matches = node.match(urlPattern) ?? [];
            for (const raw of matches) {
                const cleaned = raw.replace(/[),.;!?]+$/, '');
                const normalized = normalizeUrl(cleaned);
                if (normalized.startsWith('http://') || normalized.startsWith('https://')) {
                    result.add(normalized);
                }
            }
            return;
        }
        if (typeof node !== 'object') {
            return;
        }

        seen.add(node);
        if (Array.isArray(node)) {
            for (const item of node) {
                walk(item);
            }
            return;
        }

        for (const value of Object.values(node as Record<string, unknown>)) {
            walk(value);
        }
    };

    walk(input);
    return Array.from(result);
}

function isLikelyVideoUrl(url: string): boolean {
    const lower = url.toLowerCase();
    if (
        lower.includes('mime_type=video') ||
        lower.includes('mime=video') ||
        lower.includes('/video/') ||
        lower.includes('video_mp4')
    ) {
        return true;
    }
    return /\.(mp4|mov|m4v|avi|wmv|webm|mkv|flv|3gp|mpeg|mpg|m3u8)(\?|#|$)/i.test(lower);
}

function getUrlFileName(url: string): string {
    try {
        const parsed = new URL(url);
        const pathPart = parsed.pathname.split('/').pop();
        if (pathPart && pathPart.length > 1) {
            return decodeURIComponent(pathPart);
        }
        return parsed.hostname;
    } catch {
        return '链接视频';
    }
}

function getPlayableSrc(item: VideoItem): string {
    if (item.source === "url") {
        return `/video-proxy?url=${encodeURIComponent(item.url)}`;
    }
    return item.url;
}

function App() {
    const [error, setError] = useState('');
    const [videos, setVideos] = useState<VideoItem[]>([]);
    const [activeVideoIndex, setActiveVideoIndex] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [playbackError, setPlaybackError] = useState('');
    const requestIdRef = useRef(0);
    const videoRef = useRef<HTMLVideoElement | null>(null);

    const activeVideo = useMemo(() => videos[activeVideoIndex] ?? null, [videos, activeVideoIndex]);
    const activeVideoSrc = useMemo(() => (activeVideo ? getPlayableSrc(activeVideo) : ""), [activeVideo]);

    const loadPreviewBySelection = async (nextSelection: SelectionState): Promise<void> => {
        const currentRequestId = ++requestIdRef.current;
        setError('');
        setPlaybackError('');

        if (!nextSelection.tableId || !nextSelection.recordId || !nextSelection.fieldId) {
            setVideos([]);
            return;
        }

        setIsLoading(true);

        try {
            const table = await bitable.base.getTableById(nextSelection.tableId);
            const cellValue = await table.getCellValue(nextSelection.fieldId, nextSelection.recordId);

            const attachments = isAttachmentArray(cellValue) ? cellValue.filter(isVideoAttachment) : [];
            let result: VideoItem[] = [];
            if (attachments.length > 0) {
                const urls = await table.getCellAttachmentUrls(
                    attachments.map((item) => item.token),
                    nextSelection.fieldId,
                    nextSelection.recordId,
                );

                if (currentRequestId !== requestIdRef.current) {
                    return;
                }

                result = attachments.map((item, idx) => ({
                    token: item.token,
                    name: item.name,
                    type: item.type,
                    size: item.size,
                    url: urls[idx] ?? '',
                    source: 'attachment',
                }));
            } else {
                const candidateUrls = extractCandidateUrls(cellValue);
                const videoUrls = candidateUrls.filter(isLikelyVideoUrl);
                if (videoUrls.length > 0) {
                    result = videoUrls.map((url, idx) => ({
                        token: `url-${idx}`,
                        name: getUrlFileName(url),
                        type: 'video/url',
                        size: 0,
                        url,
                        source: 'url',
                    }));
                }
            }

            if (result.length === 0) {
                setVideos([]);
                return;
            }

            setVideos(result);
            setActiveVideoIndex(0);
        } catch (err) {
            const message = err instanceof Error ? err.message : '未知错误';
            setVideos([]);
            setError(message);
        } finally {
            if (currentRequestId === requestIdRef.current) {
                setIsLoading(false);
            }
        }
    };

    useEffect(() => {
        let unmounted = false;
        const unsubscriber = bitable.base.onSelectionChange(async ({ data }) => {
            if (unmounted) {
                return;
            }
            await loadPreviewBySelection(data);
        });

        void bitable.base.getSelection().then(async (data) => {
            if (unmounted) {
                return;
            }
            await loadPreviewBySelection(data);
        });

        return () => {
            unmounted = true;
            unsubscriber?.();
        };
    }, []);

    useEffect(() => {
        if (!activeVideo || !videoRef.current) {
            return;
        }
        setPlaybackError('');
        const player = videoRef.current;
        (player as HTMLVideoElement & { referrerPolicy?: string }).referrerPolicy = 'no-referrer';
        void player.play().catch(() => {
            // Auto-play may still be restricted by browser policy.
        });
    }, [activeVideo]);

    return (
        <main className="container">
            <header className="header">
                <h1>视频预览</h1>
                <p>选中单元格后自动播放视频。</p>
            </header>

            {activeVideo ? (
                <section className="preview-section">
                    <video
                        ref={videoRef}
                        key={activeVideo.token}
                        className="video-player"
                        controls
                        autoPlay
                        muted
                        preload="auto"
                        playsInline
                        src={activeVideoSrc}
                        onLoadedData={() => setPlaybackError('')}
                        onError={() => {
                            setPlaybackError(
                                '当前链接无法直接播放，可能是链接过期、需要鉴权或被源站防盗链拦截。',
                            );
                        }}
                    />

                    {playbackError && (
                        <div className="playback-error">
                            <span>{playbackError}</span>
                            <a href={activeVideo.url} target="_blank" rel="noreferrer">
                                打开原链接
                            </a>
                        </div>
                    )}

                    <ul className="video-list">
                        {videos.map((item, idx) => (
                            <li key={item.token}>
                                <button
                                    type="button"
                                    className={idx === activeVideoIndex ? 'video-item active' : 'video-item'}
                                    onClick={() => setActiveVideoIndex(idx)}
                                >
                                    <span className="name">{item.name}</span>
                                    <span className="desc">
                                        {item.source === 'attachment'
                                            ? `${item.type || 'unknown'} · ${formatBytes(item.size)}`
                                            : '链接视频'}
                                    </span>
                                </button>
                            </li>
                        ))}
                    </ul>
                </section>
            ) : (
                <section className="empty-panel">
                    {isLoading ? '加载中...' : error ? `加载失败：${error}` : '选中含视频附件或视频链接的单元格后自动预览。'}
                </section>
            )}
        </main>
    );
}

export default App;

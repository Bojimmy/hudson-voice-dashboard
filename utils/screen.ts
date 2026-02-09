
export class ScreenRecorder {
    private stream: MediaStream | null = null;
    private videoTrack: MediaStreamTrack | null = null;
    private imageCapture: any | null = null; // ImageCapture is experimental in some browsers
    private onData: (base64: string) => void;
    private intervalId: any = null;
    private videoEl: HTMLVideoElement | null = null;
    private canvas: HTMLCanvasElement;
    private context: CanvasRenderingContext2D | null;

    constructor(onData: (base64: string) => void) {
        this.onData = onData;
        this.canvas = document.createElement('canvas');
        this.context = this.canvas.getContext('2d');
    }

    async start() {
        try {
            if (!navigator.mediaDevices?.getDisplayMedia) {
                throw new Error("Display capture API is unavailable in this browser/context.");
            }

            this.stream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                    frameRate: { ideal: 5 }
                },
                audio: false
            });

            this.videoTrack = this.stream.getVideoTracks()[0];

            // If the user stops sharing via the browser UI
            this.videoTrack.onended = () => {
                this.stop();
            };

            // Start capture loop
            this.startCaptureLoop();

        } catch (err: any) {
            const detail = err?.name && err?.message
                ? `${err.name}: ${err.message}`
                : (err?.message || String(err));
            console.error("ScreenRecorder Start Failed:", err);
            this.stop();
            throw new Error(`ScreenRecorder start failed. ${detail}`);
        }
    }

    private startCaptureLoop() {
        const video = document.createElement('video');
        this.videoEl = video;
        video.srcObject = this.stream;
        video.muted = true;
        video.playsInline = true;
        video.play().catch((e) => {
            console.error("Video playback failed for screen capture:", e);
        });

        // Gemini Vision suggests 1-2 fps for real-time video understanding
        this.intervalId = setInterval(async () => {
            if (!this.stream || !this.videoTrack || this.videoTrack.readyState !== 'live') return;

            try {
                // Resize if needed to keep bandwidth low
                const width = 640;
                const height = 360;

                this.canvas.width = width;
                this.canvas.height = height;

                if (this.context) {
                    if (video.readyState < 2) return;
                    this.context.drawImage(video, 0, 0, width, height);

                    // Convert to base64 JPEG (0.6 quality)
                    const base64 = this.canvas.toDataURL('image/jpeg', 0.6).split(',')[1];
                    this.onData(base64);
                }
            } catch (e) {
                console.error("Frame capture error:", e);
            }
        }, 1000); // 1 FPS
    }

    stop() {
        if (this.intervalId) clearInterval(this.intervalId);
        this.videoTrack?.stop();
        this.stream?.getTracks().forEach(t => t.stop());
        if (this.videoEl) {
            this.videoEl.srcObject = null;
        }
        this.stream = null;
        this.videoTrack = null;
        this.intervalId = null;
        this.videoEl = null;
    }
}

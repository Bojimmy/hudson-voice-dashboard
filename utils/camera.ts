export class CameraRecorder {
    private stream: MediaStream | null = null;
    private video: HTMLVideoElement;
    private canvas: HTMLCanvasElement;
    private ctx: CanvasRenderingContext2D | null;
    private intervalId: number | null = null;
    private onFrame: (base64Jpeg: string) => void;
    private maxDimension = 1024;

    constructor(onFrame: (base64Jpeg: string) => void) {
        this.onFrame = onFrame;
        this.video = document.createElement('video');
        this.video.autoplay = true;
        this.video.playsInline = true;
        this.video.muted = true;

        this.canvas = document.createElement('canvas');
        this.ctx = this.canvas.getContext('2d');
    }

    public async start(): Promise<void> {
        try {
            // Favor the back camera (or Continuity Camera on Mac)
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "environment", width: { ideal: 1280 } }
            });
            this.video.srcObject = this.stream;
            await this.video.play();

            // Capture a frame every 1 second
            this.intervalId = window.setInterval(() => this.captureFrame(), 1000);
        } catch (error) {
            console.error("Camera access denied or failed", error);
            throw error;
        }
    }

    private captureFrame() {
        if (!this.video.videoWidth || !this.ctx) return;

        // Calculate aspect ratio keeping max dimension at 1024px
        let w = this.video.videoWidth;
        let h = this.video.videoHeight;
        if (w > this.maxDimension || h > this.maxDimension) {
            if (w > h) {
                h = Math.round((h * this.maxDimension) / w);
                w = this.maxDimension;
            } else {
                w = Math.round((w * this.maxDimension) / h);
                h = this.maxDimension;
            }
        }

        this.canvas.width = w;
        this.canvas.height = h;
        this.ctx.drawImage(this.video, 0, 0, w, h);

        // Extract JPEG payload (strip the data URI prefix)
        const dataUrl = this.canvas.toDataURL('image/jpeg', 0.6);
        const base64 = dataUrl.split(',')[1];
        if (base64) {
            this.onFrame(base64);
        }
    }

    public stop(): void {
        if (this.intervalId !== null) {
            window.clearInterval(this.intervalId);
            this.intervalId = null;
        }
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        this.video.srcObject = null;
    }
}

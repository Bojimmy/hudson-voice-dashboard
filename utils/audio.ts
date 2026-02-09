
// Robust Audio Utilities for M4 Mac & Bluetooth Compatibility

export const base64ToArrayBuffer = (base64: string): ArrayBuffer => {
    const binaryString = window.atob(base64);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
};

export const arrayBufferToBase64 = (buffer: ArrayBuffer): string => {
    let binary = '';
    const bytes = new Uint8Array(buffer);
    const len = bytes.byteLength;
    for (let i = 0; i < len; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
};

export const floatTo16BitPCM = (input: Float32Array): Int16Array => {
    const output = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
        const s = Math.max(-1, Math.min(1, input[i]));
        output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return output;
};

// Resample using linear interpolation for hardware compatibility
export const resampleAudio = (
    inputData: Float32Array,
    sourceRate: number,
    targetRate: number
): Float32Array => {
    if (sourceRate === targetRate) return inputData;
    const ratio = sourceRate / targetRate;
    const newLength = Math.round(inputData.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
        const index = i * ratio;
        const floor = Math.floor(index);
        const ceil = Math.min(Math.ceil(index), inputData.length - 1);
        const t = index - floor;
        result[i] = inputData[floor] * (1 - t) + inputData[ceil] * t;
    }
    return result;
};

export class AudioRecorder {
    private context: AudioContext | null = null;
    private processor: ScriptProcessorNode | null = null;
    private stream: MediaStream | null = null;
    private input: MediaStreamAudioSourceNode | null = null;
    private onData: (data: string) => void;

    constructor(onData: (data: string) => void) {
        this.onData = onData;
    }

    async start() {
        try {
            // 1. Get stream with NO strict constraints first (Fixes M4 Mac Bluetooth issue)
            this.stream = await navigator.mediaDevices.getUserMedia({ 
                audio: true 
            });

            // 2. Initialize AudioContext AFTER stream is acquired
            this.context = new (window.AudioContext || (window as any).webkitAudioContext)();
            
            if (this.context.state === 'suspended') {
                await this.context.resume();
            }

            const sourceRate = this.context.sampleRate;
            this.input = this.context.createMediaStreamSource(this.stream);
            
            // 3. ScriptProcessor for processing raw bits
            this.processor = this.context.createScriptProcessor(4096, 1, 1);
            
            this.processor.onaudioprocess = (e) => {
                const inputData = e.inputBuffer.getChannelData(0);
                // Resample from hardware rate (usually 44.1k or 48k) to 16k
                const resampled = resampleAudio(inputData, sourceRate, 16000);
                const pcmData = floatTo16BitPCM(resampled);
                this.onData(arrayBufferToBase64(pcmData.buffer));
            };

            this.input.connect(this.processor);
            
            // Output to dummy gain to keep processor alive without feedback
            const gain = this.context.createGain();
            gain.gain.value = 0;
            this.processor.connect(gain);
            gain.connect(this.context.destination);
            
        } catch (err) {
            console.error("AudioRecorder Start Failed:", err);
            this.stop();
            throw err;
        }
    }

    stop() {
        this.input?.disconnect();
        this.processor?.disconnect();
        this.stream?.getTracks().forEach(t => t.stop());
        this.context?.close();
        
        this.input = null;
        this.processor = null;
        this.stream = null;
        this.context = null;
    }
}

export class AudioStreamer {
    public context: AudioContext;
    private nextStartTime: number = 0;
    private sources: AudioBufferSourceNode[] = [];
    public analyser: AnalyserNode;

    constructor() {
        this.context = new (window.AudioContext || (window as any).webkitAudioContext)();
        this.analyser = this.context.createAnalyser();
        this.analyser.fftSize = 256;
        this.analyser.connect(this.context.destination);
    }

    async addPCM16(chunk: ArrayBuffer) {
        if (this.context.state === 'suspended') await this.context.resume();
        
        const float32Data = new Float32Array(chunk.byteLength / 2);
        const dataView = new DataView(chunk);

        for (let i = 0; i < chunk.byteLength / 2; i++) {
            float32Data[i] = dataView.getInt16(i * 2, true) / 32768.0;
        }

        // Gemini always outputs 24000
        const audioBuffer = this.context.createBuffer(1, float32Data.length, 24000);
        audioBuffer.getChannelData(0).set(float32Data);

        const source = this.context.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(this.analyser);

        const now = this.context.currentTime;
        this.nextStartTime = Math.max(this.nextStartTime, now);
        
        source.start(this.nextStartTime);
        this.nextStartTime += audioBuffer.duration;
        this.sources.push(source);
        
        source.onended = () => {
             const idx = this.sources.indexOf(source);
             if (idx > -1) this.sources.splice(idx, 1);
        };
    }
    
    stop() {
        this.sources.forEach(s => { try { s.stop(); } catch(e) {} });
        this.sources = [];
        this.nextStartTime = this.context.currentTime;
    }

    isPlaying(): boolean {
        const queuedAhead = this.nextStartTime - this.context.currentTime;
        return this.sources.length > 0 || queuedAhead > 0.08;
    }
}

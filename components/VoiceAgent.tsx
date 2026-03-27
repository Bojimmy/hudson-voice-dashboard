import React, { useState, useEffect, useRef } from 'react';
import { GoogleGenAI, Type, FunctionDeclaration, LiveServerMessage, Modality } from "@google/genai";
import { AudioRecorder, AudioStreamer, base64ToArrayBuffer } from '../utils/audio';
import { ScreenRecorder } from '../utils/screen';
import { CameraRecorder } from '../utils/camera';
import { SYSTEM_INSTRUCTION } from '../utils/knowledge';
import Visualizer from './Visualizer';
import { ConnectionStatus } from '../types';

type MemoryRole = 'user' | 'assistant' | 'system';
type MemoryTurn = {
    ts: number;
    role: MemoryRole;
    text: string;
    source?: string;
};

const MEMORY_STORAGE_KEY = 'antigravity_gema_memory_v1';
const MEMORY_ENABLED_KEY = 'antigravity_gema_memory_enabled_v1';
const MAX_MEMORY_TURNS = 120;
const INJECT_MEMORY_TURNS = 24;

// ... (Keep existing tool definitions)
const kimiTool: FunctionDeclaration = {
    name: "consultKimi",
    description: "Engage the Kimi Action Engine for technical research, code analysis, or deep data processing.",
    parameters: {
        type: Type.OBJECT,
        properties: { query: { type: Type.STRING, description: "The technical task for Kimi." } },
        required: ["query"]
    }
};

const nanoBananaTool: FunctionDeclaration = {
    name: "nanoBanana",
    description: "Image Generation Tool. Renders high-fidelity images. Use this to execute prompts provided by the user (including those from Nano Banana Pro Builder).",
    parameters: {
        type: Type.OBJECT,
        properties: { prompt: { type: Type.STRING, description: "The visual prompt to render." } },
        required: ["prompt"]
    }
};

const formatErrorMessage = (err: unknown, fallback: string): string => {
    if (!err) return fallback;
    if (typeof err === 'string') return `${fallback}: ${err}`;
    if (typeof err === 'object') {
        const anyErr = err as { message?: string; name?: string };
        if (anyErr.message && anyErr.name) return `${fallback}: ${anyErr.name} - ${anyErr.message}`;
        if (anyErr.message) return `${fallback}: ${anyErr.message}`;
        if (anyErr.name) return `${fallback}: ${anyErr.name}`;
    }
    return fallback;
};

const estimatePeakFromBase64Pcm16 = (base64: string): number => {
    try {
        const raw = window.atob(base64);
        if (!raw || raw.length < 2) return 0;
        let maxAbs = 0;
        for (let i = 0; i + 1 < raw.length; i += 2) {
            let v = raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8);
            if (v & 0x8000) v = v - 0x10000;
            const abs = Math.abs(v);
            if (abs > maxAbs) maxAbs = abs;
        }
        return maxAbs / 32768;
    } catch {
        return 0;
    }
};

const HUDSON_OVERVIEW_BRIEF = [
    "Here is your Hudson brief overview.",
    "Hudson means Hudson's Home Staging, not a data privacy or security center.",
    "Hudson uses an agent-plus-CRM workflow so no lead gets missed.",
    "Opportunity Hunter reads inbound leads, sets priority, and drafts follow-up replies for your review.",
    "Content Flywheel creates branded marketing drafts connected to real lead context.",
    "Ad Amplifier decides what content should be boosted and logs each decision.",
    "The shared CRM tracks lead status, next action, follow-up date, and audit history in one place.",
    "You can ask in plain language, like show me emails scheduled for tomorrow or draft follow-up for Ron Wyse.",
    "It is built for Daytona Beach and Ormond Beach operations with fast daily follow-up and human approval gates.",
].join(" ");

const HUDSON_DEPLOYMENT_INTRO = [
    "System startup broadcast.",
    "Speak this exactly and then pause.",
    "Hello, I am Neural Kimi.",
    "Hudson Virtual PR Arm status: systems online and ready to deploy.",
    "Opportunity Hunter is online for lead triage and follow-up drafts.",
    "Content Flywheel is online for branded content generation.",
    "Ad Amplifier is online for boost decisions.",
    "Shared CRM orchestration is online for lead status, follow-up dates, tasks, and audit history.",
    "Google Forms creation is online.",
    "Dashboard strict safety controls are online.",
    "Voice and chat channels are online.",
    "Awaiting your command.",
].join(" ");

const isHudsonOverviewTrigger = (text: string): boolean => {
    const t = (text || "").toLowerCase().replace(/\s+/g, " ").trim();
    return (
        t.includes("kimi give me the hudson brieaf overview") ||
        t.includes("kimi give me the hudson brief overview")
    );
};

const HUDSON_OVERRIDE_COOLDOWN_MS = 5000;

const sanitizeMemoryText = (text: string): string => {
    return text.replace(/\s+/g, ' ').trim().slice(0, 700);
};

const extractModelText = (message: LiveServerMessage): string => {
    const parts = message.serverContent?.modelTurn?.parts || [];
    const textParts: string[] = [];
    for (const part of parts as any[]) {
        if (typeof part?.text === 'string' && part.text.trim()) {
            textParts.push(part.text.trim());
        }
    }
    return sanitizeMemoryText(textParts.join(' '));
};

const extractUserTranscript = (message: LiveServerMessage): string => {
    const anyMsg = message as any;
    const candidates = [
        anyMsg?.serverContent?.inputTranscription?.text,
        anyMsg?.serverContent?.inputTranscription?.transcript,
        anyMsg?.serverContent?.inputTranscriptionResult?.text,
        anyMsg?.serverContent?.userTranscript,
    ];
    for (const c of candidates) {
        if (typeof c === 'string' && c.trim()) {
            return sanitizeMemoryText(c);
        }
    }
    return '';
};

const VoiceAgent: React.FC = () => {
    const [status, setStatus] = useState<ConnectionStatus>('disconnected');
    const [isKimiThinking, setIsKimiThinking] = useState(false);
    const [kimiAvailable, setKimiAvailable] = useState<boolean>(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [isScreenSharing, setIsScreenSharing] = useState(false);
    const [isCameraSharing, setIsCameraSharing] = useState(false);
    const [generatedImage, setGeneratedImage] = useState<string | null>(null);
    const [showLargeImage, setShowLargeImage] = useState(false);
    const [memoryEnabled, setMemoryEnabled] = useState<boolean>(true);
    const [memoryTurns, setMemoryTurns] = useState<MemoryTurn[]>([]);

    const recorderRef = useRef<AudioRecorder | null>(null);
    const screenRecorderRef = useRef<ScreenRecorder | null>(null);
    const cameraRecorderRef = useRef<CameraRecorder | null>(null);
    const streamerRef = useRef<AudioStreamer | null>(null);
    const lastBargeInMsRef = useRef<number>(0);
    const lastHudsonOverrideMsRef = useRef<number>(0);
    const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
    const sessionRef = useRef<any>(null);

    useEffect(() => {
        // Force available for Local OpenClaw
        setKimiAvailable(true);
    }, []);

    useEffect(() => {
        try {
            const rawEnabled = window.localStorage.getItem(MEMORY_ENABLED_KEY);
            if (rawEnabled === 'false') setMemoryEnabled(false);
            const raw = window.localStorage.getItem(MEMORY_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (Array.isArray(parsed)) {
                    setMemoryTurns(parsed.slice(-MAX_MEMORY_TURNS));
                }
            }
        } catch (e) {
            console.warn('Memory load failed:', e);
        }
    }, []);

    useEffect(() => {
        try {
            window.localStorage.setItem(MEMORY_ENABLED_KEY, memoryEnabled ? 'true' : 'false');
        } catch (e) {
            console.warn('Memory enabled state save failed:', e);
        }
    }, [memoryEnabled]);

    useEffect(() => {
        try {
            window.localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify(memoryTurns.slice(-MAX_MEMORY_TURNS)));
        } catch (e) {
            console.warn('Memory save failed:', e);
        }
    }, [memoryTurns]);

    const pushMemoryTurn = (role: MemoryRole, text: string, source?: string) => {
        const clean = sanitizeMemoryText(text || '');
        if (!clean) return;
        setMemoryTurns(prev => [...prev, { ts: Date.now(), role, text: clean, source }].slice(-MAX_MEMORY_TURNS));
    };

    const clearMemory = () => {
        setMemoryTurns([]);
        try {
            window.localStorage.removeItem(MEMORY_STORAGE_KEY);
        } catch (e) {
            console.warn('Memory clear failed:', e);
        }
    };

    const buildMemoryContext = (): string => {
        if (!memoryEnabled || memoryTurns.length === 0) return '';
        const recent = memoryTurns.slice(-INJECT_MEMORY_TURNS);
        const lines = recent.map((t) => {
            const who = t.role.toUpperCase();
            return `${who}: ${t.text}`;
        });
        return [
            "Persistent conversation memory from prior sessions (use as context, do not quote unless asked):",
            ...lines,
        ].join('\n');
    };

    // NEW: Auto-Greet when connected
    useEffect(() => {
        if (status === 'connected' && sessionRef.current) {
            console.log("👋 Sending greeting signal...");
            const timer = setTimeout(() => {
                sessionRef.current.then((s: any) => {
                    // Try sendClientContent (Standard) or send (Legacy/Variant)
                    if (typeof s.sendClientContent === 'function') {
                        s.sendClientContent({
                            turns: [{ role: "user", parts: [{ text: HUDSON_DEPLOYMENT_INTRO }] }],
                            turnComplete: true
                        });
                    } else if (typeof s.send === 'function') {
                        s.send({ parts: [{ text: HUDSON_DEPLOYMENT_INTRO }], turnComplete: true });
                    } else {
                        console.warn("⚠️ Could not find send/sendClientContent method on session object:", s);
                    }
                }).catch((e: any) => console.error("Greeting failed:", e));
            }, 1000);
            return () => clearTimeout(timer);
        }
    }, [status]);

    const callKimiAPI = async (query: string): Promise<string> => {
        // ... (Keep existing Kimi API logic)
        // OPENCLAW CONFIGURATION (Local Kimi)
        const localToken = "748e284d1c1ad7065cc32525784fb6c3fe61ae63b0d79902";
        try {
            pushMemoryTurn('user', query, 'consultKimi');
            // ORIGINAL MOONSHOT API (Disabled)
            /*
            const response = await fetch("https://api.moonshot.ai/v1/chat/completions", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Authorization": `Bearer ${kimiKey}` },
                body: JSON.stringify({
                    model: "moonshot-v1-8k",
                    messages: [{ role: "system", content: "You are the Kimi Action Engine. Provide high-density technical analysis." }, { role: "user", content: query }],
                    temperature: 0.1
                })
            });
            */

            // NEW LOCAL OPENCLAW GATEWAY (Via Proxy to avoid CORS)
            const response = await fetch("/openclaw/v1/chat/completions", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${localToken}`
                },
                body: JSON.stringify({
                    model: "moonshot/kimi-k2.5", // OpenClaw uses this model name
                    messages: [
                        { role: "system", content: "You are Kimi. When performing research, ALWAYS save the output to a file (markdown) immediately. Do not ask for permission. Respond with a brief summary and the filename." },
                        { role: "user", content: query }
                    ],
                    temperature: 0.1
                })
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`HTTP ${response.status}: ${text.substring(0, 50)}`);
            }
            const data = await response.json();
            const content = data.choices?.[0]?.message?.content || "No data returned.";
            pushMemoryTurn('assistant', content, 'consultKimi');
            return content;
        } catch (e: any) {
            console.error(e);
            return `SYSTEM ALERT: Connection to Kimi failed. Details: ${e.message}. Check if OpenClaw is running.`;
        }
    };

    const generateNanoImage = async (prompt: string): Promise<string> => {
        const apiKey = process.env.API_KEY || process.env.NEXT_PUBLIC_GEMINI_API_KEY;
        if (!apiKey) return "Error: API Key missing.";

        try {
            // Use the SDK to generate image
            const client = new GoogleGenAI({ apiKey });

            // Note: Adapting to likely SDK method signature or REST fallback
            // Trying standard new SDK method
            const response = await client.models.generateImages({
                model: 'imagen-4.0-generate-001',
                prompt: prompt,
                config: { numberOfImages: 1 }
            });

            if (response.generatedImages && response.generatedImages.length > 0) {
                // Assuming base64 is returned in the image property or byte conversion needed
                const imgData = response.generatedImages[0].image.imageBytes;
                // Convert to valid base64 src
                const base64Image = `data:image/png;base64,${imgData}`;
                setGeneratedImage(base64Image);
                return "Image generated successfully and displayed on screen.";
            }
            return "Generation failed: No image returned.";

        } catch (e: any) {
            console.error("NanoBanana Error:", e);
            return `Image generation failed: ${e.message}`;
        }
    };

    const connect = async () => {
        try {
            setStatus('connecting');
            setErrorMsg(null);

            const apiKey = process.env.API_KEY || process.env.NEXT_PUBLIC_GEMINI_API_KEY; // Support both env vars
            if (!apiKey) throw new Error("API Key missing");

            const ai = new GoogleGenAI({ apiKey });
            const memoryContext = buildMemoryContext();
            const effectiveInstruction = memoryContext
                ? `${SYSTEM_INSTRUCTION}\n\n${memoryContext}`
                : SYSTEM_INSTRUCTION;

            const streamer = new AudioStreamer();
            streamerRef.current = streamer;
            setAnalyser(streamer.analyser);

            const sessionPromise = ai.live.connect({
                model: 'gemini-2.5-flash-native-audio-preview-12-2025',
                config: {
                    systemInstruction: effectiveInstruction,
                    tools: [{ functionDeclarations: [kimiTool, nanoBananaTool] }],
                    responseModalities: [Modality.AUDIO],
                    speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: 'Kore' } } },
                },
                callbacks: {
                    onopen: () => setStatus('connected'),
                    onmessage: async (message: LiveServerMessage) => {
                        const userTranscript = extractUserTranscript(message);
                        if (userTranscript) pushMemoryTurn('user', userTranscript, 'voice');
                        if (userTranscript && isHudsonOverviewTrigger(userTranscript)) {
                            const now = Date.now();
                            if (now - lastHudsonOverrideMsRef.current > HUDSON_OVERRIDE_COOLDOWN_MS) {
                                lastHudsonOverrideMsRef.current = now;
                                console.log("🧭 HUDSON OVERRIDE: transcript trigger detected; forcing local brief.");
                                streamer.stop();
                                const forcedPrompt = [
                                    "SYSTEM OVERRIDE:",
                                    "User requested Hudson brief overview.",
                                    "Respond immediately in plain speech with this exact content and no additions:",
                                    HUDSON_OVERVIEW_BRIEF,
                                ].join(" ");
                                try {
                                    await sessionRef.current.then((s: any) => {
                                        if (typeof s.sendClientContent === 'function') {
                                            s.sendClientContent({
                                                turns: [{ role: "user", parts: [{ text: forcedPrompt }] }],
                                                turnComplete: true
                                            });
                                        } else if (typeof s.send === 'function') {
                                            s.send({ parts: [{ text: forcedPrompt }], turnComplete: true });
                                        }
                                    });
                                } catch (e) {
                                    console.warn("Hudson override send failed:", e);
                                }
                            }
                        }
                        const assistantText = extractModelText(message);
                        if (assistantText) pushMemoryTurn('assistant', assistantText, 'voice');

                        try {
                            if (message.serverContent?.modelTurn?.parts) {
                                message.serverContent.modelTurn.parts.forEach((part: any) => {
                                    if (part.inlineData && part.inlineData.data) {
                                        const mimeType = String(part.inlineData.mimeType || '');
                                        if (mimeType.startsWith('audio/pcm')) {
                                            const buffer = base64ToArrayBuffer(part.inlineData.data);
                                            if (buffer.byteLength > 0) {
                                                streamer.addPCM16(buffer);
                                            }
                                        }
                                    }
                                });
                            }
                        } catch (audioErr) {
                            console.error("Audio playback error:", audioErr);
                        }
                        if (message.serverContent?.interrupted) {
                            streamer.stop();
                        }
                        if (message.toolCall) {
                            // ... (tool handling)
                            const functionResponses = await Promise.all(message.toolCall.functionCalls.map(async (fc) => {
                                if (fc.name === "consultKimi") {
                                    const kimiQuery = String((fc.args as any)?.query || "");
                                    if (isHudsonOverviewTrigger(kimiQuery)) {
                                        console.log("🧭 HUDSON OVERRIDE: bypassing Kimi API for local brief.");
                                        const localAnswer = HUDSON_OVERVIEW_BRIEF;
                                        pushMemoryTurn('assistant', localAnswer, 'local-hudson-overview');
                                        return { id: fc.id, name: fc.name, response: { result: localAnswer } };
                                    }
                                    console.log("🚀 KIMI TRIGGERED");
                                    setIsKimiThinking(true);
                                    const answer = await callKimiAPI(kimiQuery);
                                    console.log("✅ KIMI RESPONSE");
                                    setIsKimiThinking(false);
                                    return { id: fc.id, name: fc.name, response: { result: answer } };
                                }
                                if (fc.name === "nanoBanana") {
                                    console.log("🍌 NANOBANANA TRIGGERED");
                                    setIsKimiThinking(true);
                                    const result = await generateNanoImage((fc.args as any).prompt);
                                    setIsKimiThinking(false);
                                    return { id: fc.id, name: fc.name, response: { result } };
                                }
                                return { id: fc.id, name: fc.name, response: { status: "ok" } };
                            }));
                            sessionRef.current.then((s: any) => s.sendToolResponse({ functionResponses }));
                        }
                    },
                    onclose: () => disconnect(),
                    onerror: (err) => {
                        console.error(err);
                        setErrorMsg(formatErrorMessage(err, "Live Session Error"));
                        disconnect();
                    }
                }
            });

            sessionRef.current = sessionPromise;


            try {
                const recorder = new AudioRecorder((base64) => {
                    const streamer = streamerRef.current;
                    const now = Date.now();
                    if (streamer && streamer.isPlaying() && (now - lastBargeInMsRef.current) > 500) {
                        const peak = estimatePeakFromBase64Pcm16(base64);
                        if (peak >= 0.22) {
                            // Local barge-in: user started speaking while assistant audio was playing.
                            streamer.stop();
                            lastBargeInMsRef.current = now;
                        }
                    }
                    sessionPromise.then((s: any) => {
                        try {
                            s.sendRealtimeInput({ media: { mimeType: 'audio/pcm;rate=16000', data: base64 } });
                        } catch (e) {
                            console.warn("Audio frame drop:", e);
                        }
                    });
                });
                await recorder.start();
                recorderRef.current = recorder;
            } catch (micErr) {
                console.warn("⚠️ Microphone Access Failed, proceeding in Speaker Mode:", micErr);
                setErrorMsg("Mic Failed (Speaker Mode)");
                // Do not throw here, allow connection to persist for output
            }

        } catch (err: any) {
            console.error(err);
            setStatus('error');
            if (!errorMsg || errorMsg !== "Mic Failed (Speaker Mode)") {
                setErrorMsg(formatErrorMessage(err, "Connection Failed"));
            }
            disconnect();
        }
    };

    const toggleScreenShare = async () => {
        if (isScreenSharing) {
            screenRecorderRef.current?.stop();
            screenRecorderRef.current = null;
            setIsScreenSharing(false);
        } else {
            try {
                const sc = new ScreenRecorder((base64) => {
                    if (sessionRef.current) {
                        sessionRef.current.then((s: any) => {
                            try {
                                s.sendRealtimeInput({ media: { mimeType: 'image/jpeg', data: base64 } });
                            } catch (e) {
                                console.warn("Screen frame drop:", e);
                            }
                        });
                    }
                });
                await sc.start();
                screenRecorderRef.current = sc;
                setIsScreenSharing(true);
            } catch (err) {
                console.error("Screen Share Error:", err);
                setErrorMsg(formatErrorMessage(err, "Screen Share Error"));
                setIsScreenSharing(false);
            }
        }
    };

    const toggleCamera = async () => {
        if (isCameraSharing) {
            cameraRecorderRef.current?.stop();
            cameraRecorderRef.current = null;
            setIsCameraSharing(false);
        } else {
            if (isScreenSharing) {
                await toggleScreenShare();
            }
            try {
                const crc = new CameraRecorder((base64) => {
                    if (sessionRef.current) {
                        sessionRef.current.then((s: any) => {
                            if (typeof s.sendRealtimeInput === 'function') {
                                try {
                                    s.sendRealtimeInput({ media: { mimeType: 'image/jpeg', data: base64 } });
                                } catch (e) {
                                    console.warn("Camera frame drop:", e);
                                }
                            } else if (typeof s.sendClientContent === 'function') {
                                try {
                                    s.sendClientContent({
                                        turns: [{
                                            role: "user",
                                            parts: [{ inlineData: { mimeType: "image/jpeg", data: base64 } }]
                                        }],
                                        turnComplete: false
                                    });
                                } catch (e) {
                                    console.warn("Camera frame drop (ClientContent):", e);
                                }
                            }
                        });
                    }
                });
                await crc.start();
                cameraRecorderRef.current = crc;
                setIsCameraSharing(true);
            } catch (err) {
                console.error("Camera Error:", err);
                setErrorMsg(formatErrorMessage(err, "Camera Access Error"));
                setIsCameraSharing(false);
            }
        }
    };

    const disconnect = () => {
        recorderRef.current?.stop();
        recorderRef.current = null;
        screenRecorderRef.current?.stop();
        screenRecorderRef.current = null;
        cameraRecorderRef.current?.stop();
        cameraRecorderRef.current = null;
        if (sessionRef.current) {
            sessionRef.current.then((s: any) => s.close());
            sessionRef.current = null;
        }
        setStatus('disconnected');
        setIsKimiThinking(false);
        setIsScreenSharing(false);
        setIsCameraSharing(false);
        setAnalyser(null);
        streamerRef.current?.stop();
    };

    return (
        <div className="flex flex-col items-center justify-center p-8 bg-slate-900/95 backdrop-blur-3xl border border-cyan-500/30 rounded-[3rem] shadow-[0_40px_120px_-20px_rgba(0,0,0,0.9)] w-full max-w-[360px] border-t-cyan-400/50 relative">

            {/* Status Indicators */}
            <div className="absolute top-6 right-8 flex flex-col gap-2 items-end">
                <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full transition-all duration-500 ${kimiAvailable ? 'bg-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.8)]' : 'bg-slate-700'}`}></div>
                    <span className={`text-[8px] font-black uppercase tracking-widest ${kimiAvailable ? 'text-cyan-400/70' : 'text-slate-600'}`}>
                        Kimi {kimiAvailable ? 'Online' : 'Offline'}
                    </span>
                </div>
                {/* Vision Indicator */}
                {status === 'connected' && (
                    <div className="flex flex-col gap-2 items-end">
                        <button onClick={toggleScreenShare} className={`flex items-center gap-2 transition-all duration-300 ${isScreenSharing ? 'opacity-100' : 'opacity-50 hover:opacity-80'}`}>
                            <div className={`w-2 h-2 rounded-full transition-all duration-500 ${isScreenSharing ? 'bg-purple-400 shadow-[0_0_10px_rgba(192,132,252,0.8)]' : 'bg-slate-700'}`}></div>
                            <span className={`text-[8px] font-black uppercase tracking-widest ${isScreenSharing ? 'text-purple-400' : 'text-slate-500'}`}>
                                Screen {isScreenSharing ? 'Active' : 'Off'}
                            </span>
                        </button>
                        <button onClick={toggleCamera} className={`flex items-center gap-2 transition-all duration-300 ${isCameraSharing ? 'opacity-100' : 'opacity-50 hover:opacity-80'}`}>
                            <div className={`w-2 h-2 rounded-full transition-all duration-500 ${isCameraSharing ? 'bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.8)]' : 'bg-slate-700'}`}></div>
                            <span className={`text-[8px] font-black uppercase tracking-widest ${isCameraSharing ? 'text-emerald-400' : 'text-slate-500'}`}>
                                Camera {isCameraSharing ? 'Active' : 'Off'}
                            </span>
                        </button>
                    </div>
                )}
            </div>

            {/* Main Interface */}
            <div className="relative w-48 h-48 flex items-center justify-center mb-8 mt-4">
                {status === 'connected' && (
                    <div className="absolute inset-0 rounded-full bg-cyan-500/10 animate-[ping_4s_linear_infinite]"></div>
                )}
                <div className={`relative z-10 w-full h-full rounded-full border-[3px] flex items-center justify-center transition-all duration-700 ${status === 'connected'
                    ? 'border-cyan-400/60 bg-slate-950 shadow-[0_0_60px_rgba(34,211,238,0.2)]'
                    : status === 'error' ? 'border-red-500/50 bg-slate-900' : 'border-white/5 bg-slate-900/60 hover:border-cyan-500/40'
                    }`}>
                    {status === 'connecting' ? (
                        <div className="w-10 h-10 border-[3px] border-cyan-500/20 border-t-cyan-400 rounded-full animate-spin"></div>
                    ) : status === 'connected' ? (
                        <div className="flex flex-col items-center">
                            {isKimiThinking ? (
                                <div className="relative flex flex-col items-center">
                                    <svg className="w-16 h-16 text-indigo-400 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                    </svg>
                                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-[8px] font-black text-indigo-300 tracking-tighter">KIMI</div>
                                </div>
                            ) : (
                                <svg onClick={toggleScreenShare} className={`w-20 h-20 transition-all duration-500 cursor-pointer ${isScreenSharing ? 'text-purple-400 drop-shadow-[0_0_25px_rgba(192,132,252,0.6)]' : 'text-cyan-400 drop-shadow-[0_0_15px_rgba(34,211,238,0.6)]'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    {isScreenSharing ? (
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                    ) : (
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
                                    )}
                                </svg>
                            )}
                        </div>
                    ) : (
                        <button onClick={connect} className="group flex flex-col items-center gap-3">
                            <div className="w-24 h-24 rounded-full bg-cyan-500/5 group-hover:bg-cyan-500/10 transition-all flex items-center justify-center border border-cyan-500/10 group-hover:border-cyan-400/30">
                                <svg className="w-12 h-12 text-cyan-500/30 group-hover:text-cyan-400 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                                </svg>
                            </div>
                            <span className="text-[10px] font-black tracking-[0.4em] uppercase text-cyan-500/30 group-hover:text-cyan-400 transition-colors">Initialize</span>
                        </button>
                    )}
                </div>
            </div>

            {/* Footer Status */}
            <div className="text-center mb-6 h-10 flex items-center justify-center">
                {errorMsg ? (
                    <div className="text-red-400 text-[10px] font-black uppercase tracking-widest bg-red-500/10 px-5 py-2 rounded-full border border-red-500/20">
                        {errorMsg}
                    </div>
                ) : (
                    <div className={`text-[11px] font-bold tracking-[0.3em] uppercase transition-all duration-500 ${status === 'connected' ? (isScreenSharing ? 'text-purple-400' : isCameraSharing ? 'text-emerald-400' : 'text-cyan-400') : 'text-slate-600'}`}>
                        {status === 'connected' ? (isKimiThinking ? 'Executing Kimi Routine' : (isScreenSharing ? 'Screen Link Active' : isCameraSharing ? 'Camera Link Active' : 'Audio Link Synced')) : 'Antigravity Voice Engine'}
                    </div>
                )}
            </div>

            <div className={`w-full transition-all duration-700 ${status === 'connected' ? 'opacity-100 h-auto min-h-[6rem] mb-8' : 'opacity-0 h-0 overflow-hidden'}`}>
                {generatedImage ? (
                    <div className="w-full h-full flex items-center justify-center animate-in fade-in zoom-in duration-500">
                        <img
                            src={generatedImage}
                            alt="NanoBanana Generation"
                            className="w-60 h-auto rounded-xl shadow-2xl border-2 border-cyan-500/50 cursor-pointer hover:brightness-110 transition-all z-50 bg-black object-cover"
                            onClick={() => setShowLargeImage(true)}
                        />
                    </div>
                ) : (
                    <Visualizer analyser={analyser} isActive={status === 'connected'} />
                )}
            </div>

            {status === 'connected' && (
                <div className="w-full flex flex-col gap-2">
                    <div className="text-[10px] text-slate-400 text-center tracking-wide">
                        Memory: {memoryEnabled ? 'On' : 'Off'} | Turns: {memoryTurns.length}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                        <button
                            onClick={() => setMemoryEnabled((v) => !v)}
                            className="py-3 bg-slate-800/30 hover:bg-cyan-500/10 hover:text-cyan-300 text-slate-400 border border-white/5 rounded-xl transition-all text-[10px] font-black uppercase tracking-[0.2em]"
                        >
                            {memoryEnabled ? 'Memory On' : 'Memory Off'}
                        </button>
                        <button
                            onClick={clearMemory}
                            className="py-3 bg-slate-800/30 hover:bg-amber-500/10 hover:text-amber-300 text-slate-400 border border-white/5 rounded-xl transition-all text-[10px] font-black uppercase tracking-[0.2em]"
                        >
                            Clear Memory
                        </button>
                    </div>
                    <button onClick={disconnect} className="w-full py-4 bg-slate-800/40 hover:bg-red-500/10 hover:text-red-400 hover:border-red-500/30 text-slate-500 border border-white/5 rounded-2xl transition-all text-[11px] font-black uppercase tracking-[0.4em]">
                        Terminate Link
                    </button>
                </div>
            )}

            {/* Fullscreen Image Overlay */}
            {generatedImage && showLargeImage && (
                <div className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-xl flex items-center justify-center p-8 animate-in fade-in duration-300" onClick={() => setShowLargeImage(false)}>
                    <img
                        src={generatedImage}
                        alt="NanoBanana Fullscreen"
                        className="max-w-full max-h-full rounded-2xl shadow-[0_0_100px_rgba(34,211,238,0.3)] border border-cyan-500/30 object-contain cursor-zoom-out"
                    />
                    <button className="absolute top-8 right-8 text-white/50 hover:text-white transition-colors">
                        <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                    <div className="absolute bottom-10 left-0 right-0 text-center animate-bounce">
                        <span className="text-cyan-400 font-mono text-xs bg-slate-900/80 px-6 py-3 rounded-full border border-cyan-500/30 shadow-lg tracking-widest uppercase">Click anywhere to close</span>
                    </div>
                </div>
            )}
        </div>
    );
};

export default VoiceAgent;

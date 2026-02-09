import React, { useEffect, useRef } from 'react';

interface VisualizerProps {
    analyser: AnalyserNode | null;
    isActive: boolean;
}

const Visualizer: React.FC<VisualizerProps> = ({ analyser, isActive }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        if (!canvasRef.current || !analyser) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        let animationId: number;
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        const draw = () => {
            animationId = requestAnimationFrame(draw);

            analyser.getByteFrequencyData(dataArray);

            canvas.width = canvas.offsetWidth;
            canvas.height = canvas.offsetHeight;
            
            const width = canvas.width;
            const height = canvas.height;

            ctx.clearRect(0, 0, width, height);

            if (!isActive) return;

            const barWidth = (width / bufferLength) * 2.5;
            let x = 0;

            for (let i = 0; i < bufferLength; i++) {
                const barHeight = (dataArray[i] / 255) * height * 0.8;

                // High-Tech Theme: Cyan to Indigo
                const gradient = ctx.createLinearGradient(0, height, 0, height - barHeight);
                gradient.addColorStop(0, 'rgba(34, 211, 238, 0.3)');
                gradient.addColorStop(1, 'rgba(129, 140, 248, 0.8)');

                ctx.fillStyle = gradient;
                ctx.fillRect(x, height - barHeight, barWidth, barHeight);
                
                x += barWidth + 3;
            }
        };

        draw();

        return () => {
            cancelAnimationFrame(animationId);
        };
    }, [analyser, isActive]);

    return (
        <canvas 
            ref={canvasRef} 
            className="w-full h-full rounded-2xl bg-slate-950/60 border border-cyan-500/10 shadow-inner"
        />
    );
};

export default Visualizer;
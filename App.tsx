import React from 'react';
import VoiceAgent from './components/VoiceAgent';

const App: React.FC = () => {
    return (
        <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-[#020617] via-[#0f172a] to-[#020617] text-slate-200 relative overflow-hidden">
            
            {/* Advanced Ambient UI Glows */}
            <div className="absolute top-0 left-0 w-full h-full pointer-events-none">
                <div className="absolute top-0 right-0 w-[800px] h-[800px] bg-cyan-500/5 rounded-full blur-[160px] opacity-40"></div>
                <div className="absolute bottom-0 left-0 w-[800px] h-[800px] bg-indigo-500/5 rounded-full blur-[160px] opacity-40"></div>
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[1200px] h-[1200px] bg-blue-500/[0.02] rounded-full blur-[200px]"></div>
            </div>

            <div className="relative z-10 w-full flex justify-center px-4">
                <VoiceAgent />
            </div>

            <div className="absolute bottom-10 flex flex-col items-center gap-2">
                <div className="text-cyan-500/40 text-[10px] tracking-[0.6em] uppercase font-black">
                    Antigravity IDE • Voice Engine
                </div>
                <div className="text-slate-700 text-[8px] tracking-[0.2em] font-medium uppercase">
                    Integrated with Kimi Action Engine
                </div>
            </div>
        </div>
    );
};

export default App;
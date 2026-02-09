export interface AudioConfig {
    sampleRate: number;
    channels: number;
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface LogMessage {
    role: 'user' | 'system' | 'model';
    text: string;
    timestamp: Date;
}

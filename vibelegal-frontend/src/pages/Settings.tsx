import { useState, useEffect } from 'react';
import { Card } from '../components/ui/Card';
import { Input } from '../components/ui/Input';
import { Button } from '../components/ui/Button';

export const Settings = () => {
    const [provider, setProvider] = useState('gemini');
    const [apiKey, setApiKey] = useState('');
    const [selectedModel, setSelectedModel] = useState('');
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [status, setStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
    const [errorMessage, setErrorMessage] = useState('');

    useEffect(() => {
        // Load from local storage
        const storedKey = localStorage.getItem('vibe_api_key');
        const storedModel = localStorage.getItem('vibe_model');
        if (storedKey) setApiKey(storedKey);
        if (storedModel) setSelectedModel(storedModel);
    }, []);

    const handleTestConnection = async () => {
        setStatus('testing');
        setErrorMessage('');

        try {
            const response = await fetch('http://localhost:8000/api/test-connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: apiKey, provider })
            });

            const data = await response.json();

            if (data.success) {
                setStatus('success');
                setAvailableModels(data.models || []);
                // Select first model if none selected or current not in list
                if (!selectedModel || (data.models && !data.models.includes(selectedModel))) {
                    if (data.models && data.models.length > 0) {
                        setSelectedModel(data.models[0]);
                    }
                }
            } else {
                setStatus('error');
                setErrorMessage(data.error || 'Connection failed');
            }
        } catch (e) {
            setStatus('error');
            setErrorMessage(e instanceof Error ? e.message : 'Network error');
        }
    };

    const handleSave = () => {
        localStorage.setItem('vibe_api_key', apiKey);
        localStorage.setItem('vibe_model', selectedModel);
        alert('Settings saved');
    };

    return (
        <div className="space-y-6">
            <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-neutral-900">Settings</h2>

            <Card title="AI Provider Connection">
                <div className="space-y-6">
                    {/* Provider Selection (Static for now as requested) */}
                    <div className="space-y-1">
                        <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium">
                            AI Provider
                        </label>
                        <select
                            className="w-full px-4 py-3.5 text-[14px] text-neutral-800 bg-neutral-50 border border-neutral-200 rounded-xl outline-none focus:bg-white focus:border-neutral-400 transition-colors"
                            value={provider}
                            onChange={(e) => setProvider(e.target.value)}
                        >
                            <option value="gemini">Google Gemini</option>
                            <option value="anthropic" disabled>Anthropic Claude (Coming Soon)</option>
                            <option value="openai" disabled>OpenAI (Coming Soon)</option>
                        </select>
                    </div>

                    <div className="flex gap-4 items-end">
                        <div className="flex-1">
                            <Input
                                label="API Key"
                                type="password"
                                placeholder="Enter your API key"
                                value={apiKey}
                                onChange={(e) => setApiKey(e.target.value)}
                            />
                        </div>
                        <Button
                            variant="secondary"
                            onClick={handleTestConnection}
                            disabled={!apiKey || status === 'testing'}
                            className="mb-1"
                        >
                            {status === 'testing' ? 'Testing...' : 'Test Connection'}
                        </Button>
                    </div>

                    {status === 'success' && availableModels.length > 0 && (
                        <div className="space-y-1 animate-in fade-in slide-in-from-top-2 duration-300">
                            <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium">
                                Model
                            </label>
                            <div className="flex items-center gap-2">
                                <select
                                    className="w-full px-4 py-3.5 text-[14px] text-neutral-800 bg-neutral-50 border border-neutral-200 rounded-xl outline-none focus:bg-white focus:border-neutral-400 transition-colors"
                                    value={selectedModel}
                                    onChange={(e) => setSelectedModel(e.target.value)}
                                >
                                    {availableModels.map(m => (
                                        <option key={m} value={m}>{m}</option>
                                    ))}
                                </select>
                                <span className="text-emerald-600 text-[13px] font-medium px-2">Connected</span>
                            </div>
                        </div>
                    )}

                    {status === 'error' && (
                        <div className="p-3 bg-red-50 text-red-600 text-[13px] rounded-lg border border-red-100">
                            Error: {errorMessage}
                        </div>
                    )}

                    <div className="pt-4 border-t border-neutral-100">
                        <Button onClick={handleSave} fullWidth disabled={!selectedModel}>
                            Save Settings
                        </Button>
                    </div>
                </div>
            </Card>

            {/* Informational card */}
            <Card>
                <h3 className="text-[15px] font-semibold text-neutral-900 mb-2">About Connection</h3>
                <p className="text-[13px] text-neutral-600 leading-relaxed">
                    VibeLegal Server connects directly to AI providers from this server.
                    Your API key is stored locally in your browser and sent to the server with each request.
                    The server does not persist your API key.
                </p>
            </Card>
        </div>
    );
};

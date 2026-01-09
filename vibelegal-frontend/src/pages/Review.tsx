import { useState, useEffect, useRef } from 'react';
import { Card } from '../components/ui/Card';
import { FileUpload } from '../components/FileUpload';
import { Button } from '../components/ui/Button';

interface JobStatus {
    id: string;
    status: 'queued' | 'processing' | 'complete' | 'error';
    progress: number;
    current_phase: string;
    operations_complete: number;
    operations_total: number;
    errors: string[];
}

export const Review = () => {
    const [file, setFile] = useState<File | null>(null);
    const [playbooks, setPlaybooks] = useState<{ id: string, name: string }[]>([]);
    const [selectedPlaybook, setSelectedPlaybook] = useState('');
    const [job, setJob] = useState<JobStatus | null>(null);
    const [isStarting, setIsStarting] = useState(false);

    const pollInterval = useRef<number | undefined>(undefined);

    // Fetch playbooks on mount
    useEffect(() => {
        fetch('http://localhost:8000/api/playbooks')
            .then(res => res.json())
            .then(data => {
                setPlaybooks(data.playbooks || []);
                if (data.playbooks && data.playbooks.length > 0) {
                    setSelectedPlaybook(data.playbooks[0].id);
                }
            })
            .catch(console.error);
    }, []);

    // Poll job status
    useEffect(() => {
        if (job && (job.status === 'queued' || job.status === 'processing')) {
            pollInterval.current = window.setInterval(async () => {
                try {
                    const res = await fetch(`http://localhost:8000/api/status/${job.id}`);
                    const data = await res.json();
                    setJob(data);

                    if (data.status === 'complete' || data.status === 'error') {
                        clearInterval(pollInterval.current);
                    }
                } catch (e) {
                    console.error("Polling error", e);
                }
            }, 2000);
        }
        return () => clearInterval(pollInterval.current);
    }, [job?.id, job?.status]); // Re-run effect when status changes mainly to stop/start logic correct? Actually logic above handles it. But we depend on job.status to start polling.

    const handleRun = async () => {
        if (!file || !selectedPlaybook) return;

        // Get settings
        const apiKey = localStorage.getItem('vibe_api_key');
        const model = localStorage.getItem('vibe_model');

        if (!apiKey || !model) {
            alert("Please configure API Key in Settings first");
            return;
        }

        setIsStarting(true);

        const formData = new FormData();
        formData.append('file', file);
        formData.append('playbookId', selectedPlaybook);
        formData.append('apiKey', apiKey);
        formData.append('model', model);
        formData.append('aiProvider', 'gemini'); // Default for now

        try {
            const res = await fetch('http://localhost:8000/api/process', {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                const data = await res.json();
                setJob({
                    id: data.job_id,
                    status: 'queued',
                    progress: 0,
                    current_phase: 'Queued',
                    operations_complete: 0,
                    operations_total: 0,
                    errors: []
                });
            } else {
                const err = await res.text();
                alert(`Failed to start job: ${err}`);
            }
        } catch (e) {
            alert("Network error starting job");
        } finally {
            setIsStarting(false);
        }
    };

    const handleDownload = () => {
        if (!job) return;
        window.open(`http://localhost:8000/api/download/${job.id}`, '_blank');
    };

    return (
        <div className="space-y-8">
            {/* Note: No page title here, Layout has logo. Or maybe "New Analysis"? */}

            <div className="space-y-6">
                {/* 1. Document */}
                <div>
                    <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                        Document
                    </label>
                    <FileUpload
                        onFileSelect={setFile}
                        selectedFile={file}
                        onClear={() => {
                            setFile(null);
                            setJob(null); // Reset job if file cleared?
                        }}
                    />
                </div>

                {/* 2. Playbook */}
                <div>
                    <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                        Playbook
                    </label>
                    <select
                        className="w-full px-4 py-3.5 text-[14px] text-neutral-800 bg-neutral-50 border border-neutral-200 rounded-xl outline-none focus:bg-white focus:border-neutral-400 transition-colors appearance-none"
                        value={selectedPlaybook}
                        onChange={e => setSelectedPlaybook(e.target.value)}
                    >
                        {playbooks.map(pb => (
                            <option key={pb.id} value={pb.id}>{pb.name}</option>
                        ))}
                        {playbooks.length === 0 && <option disabled>No playbooks available</option>}
                    </select>
                </div>

                {/* 3. Run Button */}
                <Button
                    fullWidth
                    onClick={handleRun}
                    disabled={!file || !selectedPlaybook || isStarting || job?.status === 'processing'}
                >
                    {isStarting ? 'Starting...' : 'Run Analysis'}
                </Button>
            </div>

            {/* 4. Results / Status */}
            {job && (
                <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                    <Card className={job.status === 'complete' ? "border-emerald-200 bg-emerald-50/10" : ""}>
                        <div className="space-y-4">
                            <div className="flex justify-between items-start">
                                <div>
                                    <div className="text-[11px] font-mono text-neutral-400 mb-1">ID: {job.id.slice(0, 8)}...</div>
                                    <h3 className="text-[15px] font-semibold text-neutral-900">
                                        {job.status === 'complete' ? 'Analysis Complete' : 'Processing...'}
                                    </h3>
                                </div>
                                {job.status === 'complete' && (
                                    <span className="bg-emerald-100 text-emerald-700 text-[11px] font-bold px-2 py-1 rounded">COMPLETE</span>
                                )}
                                {job.status === 'error' && (
                                    <span className="bg-red-100 text-red-700 text-[11px] font-bold px-2 py-1 rounded">ERROR</span>
                                )}
                            </div>

                            {/* Progress Bar */}
                            {(job.status === 'processing' || job.status === 'queued') && (
                                <div className="space-y-2">
                                    <div className="flex justify-between text-[12px] text-neutral-500">
                                        <span>{job.current_phase}</span>
                                        <span>{job.progress}%</span>
                                    </div>
                                    <div className="h-2 w-full bg-neutral-100 rounded-full overflow-hidden">
                                        <div
                                            className="h-full bg-neutral-900 transition-all duration-500 ease-out"
                                            style={{ width: `${job.progress}%` }}
                                        />
                                    </div>
                                </div>
                            )}

                            {/* Stats */}
                            {job.status === 'processing' && (
                                <div className="text-[12px] text-neutral-500">
                                    Operations: {job.operations_complete} / {job.operations_total || '-'}
                                </div>
                            )}

                            {/* Errors */}
                            {job.errors && job.errors.length > 0 && (
                                <div className="p-3 bg-red-50 text-red-600 text-[12px] rounded-lg">
                                    {job.errors.join(", ")}
                                </div>
                            )}

                            {/* Download Action */}
                            {job.status === 'complete' && (
                                <div className="pt-4 border-t border-emerald-100/50">
                                    <Button fullWidth onClick={handleDownload} className="bg-emerald-600 hover:bg-emerald-700 text-white">
                                        Download Redlined Document
                                    </Button>
                                </div>
                            )}
                        </div>
                    </Card>
                </div>
            )}
        </div>
    );
};

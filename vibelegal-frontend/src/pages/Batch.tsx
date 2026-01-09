import { useState, useEffect, useRef } from 'react';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';

interface BatchFile {
    file: File;
    jobId?: string;
    status: 'waiting' | 'processing' | 'complete' | 'error';
    progress?: number;
    phase?: string;
    error?: string;
}

interface JobStatus {
    id: string;
    status: 'queued' | 'processing' | 'complete' | 'error';
    progress: number;
    current_phase: string;
    operations_complete: number;
    operations_total: number;
    errors: string[];
}

const MAX_FILES = 5;

export const Batch = () => {
    const [files, setFiles] = useState<BatchFile[]>([]);
    const [playbooks, setPlaybooks] = useState<{ id: string, name: string }[]>([]);
    const [selectedPlaybook, setSelectedPlaybook] = useState('');
    const [isProcessing, setIsProcessing] = useState(false);
    const [currentIndex, setCurrentIndex] = useState(-1);
    const [isComplete, setIsComplete] = useState(false);

    const pollInterval = useRef<number | undefined>(undefined);
    const fileInputRef = useRef<HTMLInputElement>(null);

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

    // Poll current job status
    useEffect(() => {
        const currentFile = files[currentIndex];
        if (currentFile?.jobId && currentFile.status === 'processing') {
            pollInterval.current = window.setInterval(async () => {
                try {
                    const res = await fetch(`http://localhost:8000/api/status/${currentFile.jobId}`);
                    const data: JobStatus = await res.json();

                    setFiles(prev => prev.map((f, i) =>
                        i === currentIndex
                            ? {
                                ...f,
                                progress: data.progress,
                                phase: data.current_phase,
                                status: data.status === 'complete' ? 'complete'
                                    : data.status === 'error' ? 'error'
                                        : 'processing',
                                error: data.errors?.join(', ') || undefined
                            }
                            : f
                    ));

                    if (data.status === 'complete' || data.status === 'error') {
                        clearInterval(pollInterval.current);
                        // Move to next file
                        processNext(currentIndex + 1);
                    }
                } catch (e) {
                    console.error("Polling error", e);
                }
            }, 2000);
        }
        return () => clearInterval(pollInterval.current);
    }, [currentIndex, files]);

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFiles = Array.from(e.target.files || []);
        addFiles(selectedFiles);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        const droppedFiles = Array.from(e.dataTransfer.files);
        addFiles(droppedFiles);
    };

    const addFiles = (newFiles: File[]) => {
        const docxFiles = newFiles.filter(f => f.name.endsWith('.docx'));
        const remaining = MAX_FILES - files.length;
        const toAdd = docxFiles.slice(0, remaining).map(file => ({
            file,
            status: 'waiting' as const
        }));
        setFiles(prev => [...prev, ...toAdd]);
    };

    const removeFile = (index: number) => {
        setFiles(prev => prev.filter((_, i) => i !== index));
    };

    const formatFileSize = (bytes: number) => {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    };

    const startBatch = async () => {
        if (files.length === 0 || !selectedPlaybook) return;

        const apiKey = localStorage.getItem('vibe_api_key');
        const model = localStorage.getItem('vibe_model');

        if (!apiKey || !model) {
            alert("Please configure API Key in Settings first");
            return;
        }

        setIsProcessing(true);
        setIsComplete(false);

        // Upload all files first and get job IDs
        const updatedFiles = await Promise.all(
            files.map(async (f) => {
                const formData = new FormData();
                formData.append('file', f.file);
                formData.append('playbookId', selectedPlaybook);
                formData.append('apiKey', apiKey);
                formData.append('model', model);
                formData.append('aiProvider', 'gemini');

                try {
                    const res = await fetch('http://localhost:8000/api/process', {
                        method: 'POST',
                        body: formData
                    });

                    if (res.ok) {
                        const data = await res.json();
                        return { ...f, jobId: data.job_id, status: 'waiting' as const };
                    } else {
                        return { ...f, status: 'error' as const, error: 'Failed to upload' };
                    }
                } catch (e) {
                    return { ...f, status: 'error' as const, error: 'Network error' };
                }
            })
        );

        setFiles(updatedFiles);

        // Start processing first file
        processNext(0, updatedFiles);
    };

    const processNext = (index: number, fileList?: BatchFile[]) => {
        const list = fileList || files;

        // Find next non-error file
        let nextIndex = index;
        while (nextIndex < list.length && list[nextIndex].status === 'error') {
            nextIndex++;
        }

        if (nextIndex >= list.length) {
            // All done
            setIsProcessing(false);
            setIsComplete(true);
            setCurrentIndex(-1);
            return;
        }

        setCurrentIndex(nextIndex);
        setFiles(prev => prev.map((f, i) =>
            i === nextIndex ? { ...f, status: 'processing' } : f
        ));
    };

    const handleDownloadSingle = (jobId: string) => {
        window.open(`http://localhost:8000/api/download/${jobId}`, '_blank');
    };

    const handleDownloadAll = async () => {
        const successfulJobs = files
            .filter(f => f.status === 'complete' && f.jobId)
            .map(f => f.jobId!);

        if (successfulJobs.length === 0) return;

        try {
            const res = await fetch('http://localhost:8000/api/download-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ jobIds: successfulJobs })
            });

            if (res.ok) {
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'batch_redlines.zip';
                a.click();
                window.URL.revokeObjectURL(url);
            }
        } catch (e) {
            console.error("Download error", e);
        }
    };

    const resetBatch = () => {
        setFiles([]);
        setCurrentIndex(-1);
        setIsProcessing(false);
        setIsComplete(false);
    };

    const successCount = files.filter(f => f.status === 'complete').length;
    const errorCount = files.filter(f => f.status === 'error').length;

    return (
        <div className="space-y-8">
            {/* Upload Section */}
            {!isProcessing && !isComplete && (
                <div className="space-y-6">
                    {/* Drop Zone */}
                    <div>
                        <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                            Documents ({files.length}/{MAX_FILES})
                        </label>
                        <div
                            className="border-2 border-dashed border-neutral-200 rounded-xl p-8 text-center hover:border-neutral-400 transition-colors cursor-pointer"
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <input
                                ref={fileInputRef}
                                type="file"
                                multiple
                                accept=".docx"
                                className="hidden"
                                onChange={handleFileSelect}
                            />
                            <div className="text-neutral-400 mb-2">
                                <svg className="w-10 h-10 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                                </svg>
                            </div>
                            <p className="text-[14px] text-neutral-600">
                                Drop files here or <span className="text-neutral-900 font-medium">browse</span>
                            </p>
                            <p className="text-[12px] text-neutral-400 mt-1">
                                Upload up to {MAX_FILES} documents (.docx only)
                            </p>
                        </div>
                    </div>

                    {/* File List */}
                    {files.length > 0 && (
                        <div className="space-y-2">
                            {files.map((f, i) => (
                                <div key={i} className="flex items-center justify-between px-4 py-3 bg-neutral-50 rounded-lg">
                                    <div className="flex-1 min-w-0">
                                        <p className="text-[14px] text-neutral-800 truncate">{f.file.name}</p>
                                        <p className="text-[12px] text-neutral-400">{formatFileSize(f.file.size)}</p>
                                    </div>
                                    <button
                                        onClick={() => removeFile(i)}
                                        className="ml-4 text-neutral-400 hover:text-red-500 transition-colors"
                                    >
                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                        </svg>
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Playbook */}
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

                    {/* Process Button */}
                    <Button
                        fullWidth
                        onClick={startBatch}
                        disabled={files.length === 0 || !selectedPlaybook}
                    >
                        Process All ({files.length} document{files.length !== 1 ? 's' : ''})
                    </Button>
                </div>
            )}

            {/* Processing Section */}
            {isProcessing && (
                <div className="space-y-6">
                    <div className="text-center">
                        <h2 className="text-[18px] font-semibold text-neutral-900">
                            Processing {currentIndex + 1} of {files.length}
                        </h2>
                        {files[currentIndex] && (
                            <p className="text-[14px] text-neutral-500 mt-1">
                                {files[currentIndex].file.name}
                            </p>
                        )}
                    </div>

                    {/* Current Progress */}
                    {files[currentIndex] && (
                        <div className="space-y-2">
                            <div className="flex justify-between text-[12px] text-neutral-500">
                                <span>{files[currentIndex].phase || 'Starting...'}</span>
                                <span>{files[currentIndex].progress || 0}%</span>
                            </div>
                            <div className="h-2 w-full bg-neutral-100 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-neutral-900 transition-all duration-500 ease-out"
                                    style={{ width: `${files[currentIndex].progress || 0}%` }}
                                />
                            </div>
                        </div>
                    )}

                    {/* File Status List */}
                    <div className="space-y-2 mt-6">
                        {files.map((f, i) => (
                            <div key={i} className={`flex items-center gap-3 px-4 py-2 rounded-lg ${f.status === 'processing' ? 'bg-neutral-100' :
                                    f.status === 'complete' ? 'bg-emerald-50' :
                                        f.status === 'error' ? 'bg-red-50' : 'bg-neutral-50'
                                }`}>
                                <span className={`text-[11px] font-medium uppercase tracking-wide ${f.status === 'waiting' ? 'text-neutral-400' :
                                        f.status === 'processing' ? 'text-neutral-600' :
                                            f.status === 'complete' ? 'text-emerald-600' :
                                                'text-red-600'
                                    }`}>
                                    {f.status === 'waiting' && 'Queued'}
                                    {f.status === 'processing' && 'Active'}
                                    {f.status === 'complete' && 'Done'}
                                    {f.status === 'error' && 'Failed'}
                                </span>
                                <div className="flex-1 min-w-0">
                                    <p className={`text-[14px] truncate ${f.status === 'error' ? 'text-red-600' : 'text-neutral-800'}`}>
                                        {f.file.name}
                                    </p>
                                    {f.error && (
                                        <p className="text-[12px] text-red-500">{f.error}</p>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Results Section */}
            {isComplete && (
                <div className="space-y-6">
                    <Card className={successCount > 0 ? "border-emerald-200 bg-emerald-50/10" : "border-red-200 bg-red-50/10"}>
                        <div className="text-center py-4">
                            <h2 className="text-[18px] font-semibold text-neutral-900">
                                {successCount} of {files.length} documents processed successfully
                            </h2>
                            {errorCount > 0 && (
                                <p className="text-[14px] text-red-500 mt-1">
                                    {errorCount} document{errorCount !== 1 ? 's' : ''} failed
                                </p>
                            )}
                        </div>
                    </Card>

                    {/* Successful Files */}
                    {successCount > 0 && (
                        <div className="space-y-2">
                            <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                                Completed Documents
                            </label>
                            {files.filter(f => f.status === 'complete').map((f, i) => (
                                <div key={i} className="flex items-center justify-between px-4 py-3 bg-emerald-50 rounded-lg">
                                    <div className="flex items-center gap-3">
                                        <span className="text-emerald-600">✓</span>
                                        <p className="text-[14px] text-neutral-800">{f.file.name}</p>
                                    </div>
                                    <Button
                                        onClick={() => handleDownloadSingle(f.jobId!)}
                                        className="text-[12px] px-3 py-1.5"
                                    >
                                        Download
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Failed Files */}
                    {errorCount > 0 && (
                        <div className="space-y-2">
                            <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                                Failed Documents
                            </label>
                            {files.filter(f => f.status === 'error').map((f, i) => (
                                <div key={i} className="flex items-center gap-3 px-4 py-3 bg-red-50 rounded-lg">
                                    <span className="text-red-600">✗</span>
                                    <div>
                                        <p className="text-[14px] text-neutral-800">{f.file.name}</p>
                                        <p className="text-[12px] text-red-500">{f.error || 'Processing failed'}</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Action Buttons */}
                    <div className="space-y-3">
                        {successCount > 1 && (
                            <Button fullWidth onClick={handleDownloadAll} className="bg-emerald-600 hover:bg-emerald-700 text-white">
                                Download All as ZIP
                            </Button>
                        )}
                        <Button fullWidth onClick={resetBatch}>
                            Start New Batch
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
};

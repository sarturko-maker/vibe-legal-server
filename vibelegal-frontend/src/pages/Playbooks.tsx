import { useState, useEffect } from 'react';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { FileUpload } from '../components/FileUpload';

interface Playbook {
    id: string;
    name: string;
    description: string;
    playbookText: string;
}

// Default playbooks that cannot be deleted
const DEFAULT_PLAYBOOK_IDS = ['nda-standard', 'supply-aggressive'];

export const Playbooks = () => {
    const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
    const [loading, setLoading] = useState(true);
    const [isCreating, setIsCreating] = useState(false);

    // New playbook state
    const [newName, setNewName] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [newFile, setNewFile] = useState<File | null>(null);

    const fetchPlaybooks = async () => {
        try {
            const res = await fetch('http://localhost:8000/api/playbooks');
            const data = await res.json();
            setPlaybooks(data.playbooks || []);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchPlaybooks();
    }, []);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        const formData = new FormData();
        formData.append('name', newName);
        formData.append('description', newDesc);
        if (newFile) formData.append('file', newFile);

        try {
            const res = await fetch('http://localhost:8000/api/playbooks', {
                method: 'POST',
                body: formData
            });
            if (res.ok) {
                setIsCreating(false);
                setNewName('');
                setNewDesc('');
                setNewFile(null);
                fetchPlaybooks();
            } else {
                alert('Failed to create playbook');
            }
        } catch (e) {
            alert('Error creating playbook');
        }
    };

    const handleDelete = async (playbookId: string, playbookName: string) => {
        if (!confirm(`Delete "${playbookName}"? This cannot be undone.`)) {
            return;
        }

        try {
            const res = await fetch(`http://localhost:8000/api/playbooks/${playbookId}`, {
                method: 'DELETE'
            });
            if (res.ok) {
                fetchPlaybooks();
            } else {
                const data = await res.json();
                alert(data.error || 'Failed to delete playbook');
            }
        } catch (e) {
            alert('Error deleting playbook');
        }
    };

    const isDefaultPlaybook = (id: string) => DEFAULT_PLAYBOOK_IDS.includes(id);

    if (loading) return <div className="text-neutral-500 text-center py-12">Loading playbooks...</div>;

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-neutral-900">Playbooks</h2>
                <Button onClick={() => setIsCreating(!isCreating)} variant="secondary">
                    {isCreating ? 'Cancel' : 'New Playbook'}
                </Button>
            </div>

            {isCreating && (
                <Card className="animate-in fade-in slide-in-from-top-2">
                    <form onSubmit={handleCreate} className="space-y-6">
                        <div className="space-y-4">
                            <Input
                                label="Name"
                                value={newName}
                                onChange={e => setNewName(e.target.value)}
                                required
                                placeholder="e.g. Standard NDA"
                            />
                            <div>
                                <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-1">
                                    Description
                                </label>
                                <textarea
                                    className="w-full px-4 py-3.5 text-[14px] text-neutral-800 bg-neutral-50 border border-neutral-200 rounded-xl outline-none focus:bg-white focus:border-neutral-400 transition-colors placeholder:text-neutral-400 min-h-[80px]"
                                    value={newDesc}
                                    onChange={e => setNewDesc(e.target.value)}
                                    placeholder="Describe the purpose of this playbook..."
                                />
                            </div>
                            <div>
                                <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium mb-2">
                                    Playbook Document (Optional)
                                </label>
                                <FileUpload
                                    onFileSelect={setNewFile}
                                    selectedFile={newFile}
                                    onClear={() => setNewFile(null)}
                                    className="p-6"
                                />
                            </div>
                        </div>

                        <div className="flex justify-end gap-2 pt-2">
                            <Button type="button" variant="secondary" onClick={() => setIsCreating(false)}>Cancel</Button>
                            <Button type="submit">Create Playbook</Button>
                        </div>
                    </form>
                </Card>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {playbooks.map(pb => (
                    <Card key={pb.id} className="hover:border-neutral-300 transition-colors">
                        <div className="flex justify-between items-start">
                            <div className="flex-1">
                                <h3 className="text-[15px] font-semibold text-neutral-900">{pb.name}</h3>
                                <p className="text-[13px] text-neutral-500 mt-1 line-clamp-2">{pb.description}</p>
                            </div>
                            {!isDefaultPlaybook(pb.id) && (
                                <button
                                    onClick={() => handleDelete(pb.id, pb.name)}
                                    className="ml-4 text-neutral-300 hover:text-red-500 transition-colors"
                                    title="Delete playbook"
                                >
                                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                    </svg>
                                </button>
                            )}
                        </div>
                    </Card>
                ))}
            </div>
        </div>
    );
};

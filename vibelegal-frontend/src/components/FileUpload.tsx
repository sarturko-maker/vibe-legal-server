import React, { useRef, useState } from 'react';
import { twMerge } from 'tailwind-merge';

interface FileUploadProps {
    onFileSelect: (file: File) => void;
    selectedFile?: File | null;
    onClear?: () => void;
    className?: string;
}

export const FileUpload: React.FC<FileUploadProps> = ({
    onFileSelect,
    selectedFile,
    onClear,
    className
}) => {
    const inputRef = useRef<HTMLInputElement>(null);
    const [isDragOver, setIsDragOver] = useState(false);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);

        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            // Only accept .docx
            const file = e.dataTransfer.files[0];
            if (file.name.endsWith('.docx')) {
                onFileSelect(file);
            } else {
                alert("Please upload a .docx file");
            }
        }
    };

    const handleClick = () => {
        inputRef.current?.click();
    };

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            onFileSelect(e.target.files[0]);
        }
    };

    return (
        <div
            className={twMerge(
                "relative rounded-xl border-2 border-dashed transition-all cursor-pointer p-10 text-center",
                isDragOver
                    ? "border-neutral-400 bg-neutral-50"
                    : "border-neutral-200 bg-white hover:border-neutral-300 hover:bg-neutral-50",
                className
            )}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={selectedFile ? undefined : handleClick}
        >
            <input
                type="file"
                ref={inputRef}
                className="hidden"
                accept=".docx"
                onChange={handleChange}
            />

            {selectedFile ? (
                <div className="flex flex-col items-center animate-in fade-in zoom-in duration-200">
                    <div className="mb-2 text-[14px] font-medium text-neutral-900">
                        {selectedFile.name}
                    </div>
                    <p className="text-[12px] text-neutral-500 mb-4">
                        {(selectedFile.size / 1024).toFixed(1)} KB
                    </p>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onClear?.();
                            if (inputRef.current) inputRef.current.value = '';
                        }}
                        className="text-[12px] text-red-600 hover:text-red-700 font-medium px-3 py-1 bg-red-50 rounded-lg hover:bg-red-100 transition-colors"
                    >
                        Remove File
                    </button>
                </div>
            ) : (
                <div className="text-neutral-500">
                    <p className="text-[14px] font-medium text-neutral-700 mb-1">
                        Drop .docx file here
                    </p>
                    <p className="text-[13px] text-neutral-400">
                        or click to browse
                    </p>
                </div>
            )}
        </div>
    );
};

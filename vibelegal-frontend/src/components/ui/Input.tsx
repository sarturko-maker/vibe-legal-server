import React from 'react';
import { twMerge } from 'tailwind-merge';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
    label?: string;
    error?: string;
}

export const Input: React.FC<InputProps> = ({ className, label, error, ...props }) => {
    return (
        <div className="w-full space-y-1">
            {label && (
                <label className="block text-[11px] text-neutral-500 uppercase tracking-[0.05em] font-medium">
                    {label}
                </label>
            )}
            <input
                className={twMerge(
                    "w-full px-4 py-3.5 text-[14px] text-neutral-800 bg-neutral-50 border border-neutral-200 rounded-xl outline-none focus:bg-white focus:border-neutral-400 transition-colors placeholder:text-neutral-400",
                    error ? "border-red-500 focus:border-red-500" : "",
                    className
                )}
                {...props}
            />
            {error && <p className="text-[12px] text-red-600">{error}</p>}
        </div>
    );
};

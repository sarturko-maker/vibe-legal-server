import React from 'react';
import { twMerge } from 'tailwind-merge';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
    title?: string;
    action?: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ className, title, action, children, ...props }) => {
    return (
        <div className={twMerge("bg-white border border-neutral-200 rounded-xl p-6", className)} {...props}>
            {(title || action) && (
                <div className="flex justify-between items-center mb-4">
                    {title && <h3 className="text-[15px] font-semibold tracking-[-0.02em] text-neutral-900">{title}</h3>}
                    {action && <div>{action}</div>}
                </div>
            )}
            {children}
        </div>
    );
};

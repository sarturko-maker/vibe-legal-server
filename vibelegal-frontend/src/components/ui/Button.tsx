import React from 'react';
import { twMerge } from 'tailwind-merge';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: 'primary' | 'secondary';
    fullWidth?: boolean;
}

export const Button: React.FC<ButtonProps> = ({
    className,
    variant = 'primary',
    fullWidth = false,
    ...props
}) => {
    const baseStyles = "font-medium transition-colors disabled:opacity-30 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-neutral-400 focus:ring-offset-2";

    const variants = {
        primary: "px-6 py-4 text-[14px] text-white bg-neutral-900 rounded-xl hover:bg-neutral-800",
        secondary: "px-4 py-3 text-[13px] text-neutral-600 bg-neutral-100 rounded-lg hover:bg-neutral-200"
    };

    return (
        <button
            className={twMerge(
                baseStyles,
                variants[variant],
                fullWidth ? "w-full" : "",
                className
            )}
            {...props}
        />
    );
};

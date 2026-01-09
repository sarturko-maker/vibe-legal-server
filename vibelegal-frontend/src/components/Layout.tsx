
import { NavLink, Outlet } from 'react-router-dom';
import { twMerge } from 'tailwind-merge';

const SidebarItem = ({ to, label }: { to: string; label: string }) => (
    <NavLink
        to={to}
        className={({ isActive }) =>
            twMerge(
                "block px-4 py-2.5 text-[14px] font-medium rounded-lg transition-colors mb-1",
                isActive
                    ? "bg-neutral-900 text-white"
                    : "text-neutral-600 hover:bg-neutral-100"
            )
        }
    >
        {label}
    </NavLink>
);

export const Layout = () => {
    return (
        <div className="flex min-h-screen bg-neutral-50 text-neutral-900 font-sans">
            {/* Sidebar */}
            <aside className="fixed left-0 top-0 w-[220px] h-full bg-white border-r border-neutral-200 flex flex-col">
                {/* Logo Area */}
                <div className="p-6 border-b border-neutral-100">
                    <h1 className="text-[18px] font-bold tracking-tight text-neutral-900">Vibe Legal</h1>
                    <p className="text-[10px] uppercase tracking-[0.1em] text-neutral-400 mt-1 font-medium">Server</p>
                </div>

                {/* Navigation */}
                <nav className="flex-1 p-4">
                    <SidebarItem to="/review" label="Review" />
                    <SidebarItem to="/batch" label="Batch" />
                    <SidebarItem to="/playbooks" label="Playbooks" />
                    <SidebarItem to="/settings" label="Settings" />
                </nav>

                {/* Footer info (optional) */}
                <div className="p-6">
                    <p className="text-[10px] text-neutral-300">v0.2.0-server</p>
                </div>
            </aside>

            {/* Main Content */}
            <main className="ml-[220px] flex-1 p-8 sm:p-12">
                <div className="max-w-2xl mx-auto w-full">
                    <Outlet />
                </div>
            </main>
        </div>
    );
};

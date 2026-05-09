'use client';

import { ChatInterface } from '@/components/chat/ChatInterface';
import { ChatSidebar } from '@/components/chat/ChatSidebar';

export default function ChatPage() {
    return (
        <div className="h-full flex">
            <ChatSidebar />
            <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
                <ChatInterface />
            </div>
        </div>
    );
}

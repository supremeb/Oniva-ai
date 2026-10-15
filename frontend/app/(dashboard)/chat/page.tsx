'use client';

import { ChatInterface } from '@/components/chat/ChatInterface';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import { useEngineNav } from '@/lib/stores/engine-nav';

export default function ChatPage() {
    const { conversationId, nonce } = useEngineNav();
    return (
        <div className="h-full flex">
            <ChatSidebar />
            <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
                <ChatInterface key={nonce} conversationId={nonce > 0 ? conversationId : undefined} />
            </div>
        </div>
    );
}

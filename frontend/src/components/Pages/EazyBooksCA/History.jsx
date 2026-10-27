import React, { useState, useEffect } from "react";
import { FileText, File, Image, FileSpreadsheet, MessageSquare, MessageCircleMore, Clock } from "lucide-react";
import { historyApi } from "../../../services/api";
import ChatbotLight from "../ChatBot/ChatbotLight";

export default function History() {
    const [chats, setChats] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [showChatbotLight, setShowChatbotLight] = useState(false);
    const [selectedChatId, setSelectedChatId] = useState(null);
    const [previousMessages, setPreviousMessages] = useState([]);
    const [isContextRequired, setIsContextRequired] = useState(false);
    const [selectedChat, setSelectedChat] = useState(null);

    const getFileIcon = (fileName) => {
        if (!fileName) return <File className="w-5 h-5 text-gilt-brass" />;
        const extension = fileName.split('.').pop().toLowerCase();
        switch (extension) {
            case 'pdf':
            case 'txt':
                return <FileText className="w-5 h-5 text-gilt-brass" />;
            case 'docx':
            case 'doc':
                return <File className="w-5 h-5 text-gilt-brass" />;
            case 'jpg':
            case 'jpeg':
            case 'png':
                return <Image className="w-5 h-5 text-gilt-brass" />;
            case 'xlsx':
            case 'xls':
                return <FileSpreadsheet className="w-5 h-5 text-gilt-brass" />;
            default:
                return <File className="w-5 h-5 text-gilt-brass" />;
        }
    };

    const getChatType = (chat) => {
        if (chat.client_id) return "Client File";
        if (chat.lockr_id) return "Lockr File";
        return "General Chat";
    };

    const formatDate = (dateString) => {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    };

    const handleChatClick = async (chat) => {
        setSelectedChat(chat);
        setSelectedChatId(chat.chat_id);
        setIsContextRequired(true);
        try {
            const result = await historyApi.getChatMessages({ session_id: chat.chat_id });
            setPreviousMessages(result);
            setShowChatbotLight(true);
        } catch (err) {
            console.error('Failed to fetch messages:', err);
        }
    };

    useEffect(() => {
        const fetchChats = async () => {
            try {
                const result = await historyApi.getChatHistory({});
                setChats(result);
                setLoading(false);
            } catch (err) {
                setError(err.message);
                setLoading(false);
            }
        };

        fetchChats();
    }, []);

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gilt-brass"></div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="text-gilt-debit">Error: {error}</div>
            </div>
        );
    }

    if (showChatbotLight) {
        return (
            <ChatbotLight
                client={selectedChat?.client_id ? { client_id: selectedChat.client_id } : null}
                vault={selectedChat?.lockr_id ? { lockr_id: selectedChat.lockr_id } : null}
                previousMessages={previousMessages || {messages: []}}
                chatId={selectedChatId}
                isContextRequired={isContextRequired}
                onBack={() => {
                    setShowChatbotLight(false);
                    setPreviousMessages(null);
                    setSelectedChatId(null);
                    setIsContextRequired(false);
                    setSelectedChat(null);
                }}
            />
        );
    }

    return (
        <div className="min-h-screen p-8">
            <div className="max-w-4xl mx-auto">
                <div className="mb-8">
                    <h1 className="gl-display text-3xl text-gilt-ink">Chat History</h1>
                    <p className="text-sm text-gilt-soft">View your previous conversations</p>
                </div>

                <div className="space-y-4">
                    {chats && chats.length > 0 ? (
                        chats.map((chat) => (
                            <div
                                key={chat.chat_id}
                                className="bg-gilt-card p-6 rounded-xl transition-colors duration-150 cursor-pointer group border border-gilt-hairline hover:border-gilt-brass"
                            >
                                <div className="flex items-start space-x-4">
                                    <div className="p-3 bg-gilt-sunken border border-gilt-hairline rounded-lg">
                                        {getFileIcon(chat.chat_name)}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex justify-between items-start">
                                            <div>
                                                <h3 className="text-lg font-semibold text-gilt-ink mb-1">
                                                    {chat.chat_name || "Untitled Chat"}
                                                </h3>
                                                <div className="flex items-center text-sm text-gilt-soft space-x-4">
                                                    <span className="flex items-center font-mono tabular-nums">
                                                        <Clock className="w-4 h-4 mr-1" />
                                                        {formatDate(chat.chat_date)}
                                                    </span>
                                                    <span className="font-mono tabular-nums">ID: {chat.chat_id.slice(0, 8)}...</span>
                                                </div>
                                            </div>
                                            <div className="flex items-center space-x-2">
                                                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft border border-gilt-hairline px-3 py-1.5 rounded-full">
                                                    {getChatType(chat)}
                                                </span>
                                                <button
                                                    className="p-2 text-gilt-faint hover:text-gilt-brass transition-colors opacity-0 group-hover:opacity-100 bg-gilt-sunken border border-gilt-hairline rounded-lg"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleChatClick(chat);
                                                    }}
                                                >
                                                    <MessageSquare className="w-5 h-5" />
                                                </button>
                                            </div>
                                        </div>
                                        {(chat.client_id || chat.lockr_id) && (
                                            <div className="mt-3 flex space-x-4">
                                                {chat.client_id && (
                                                    <span className="text-xs font-mono tabular-nums text-gilt-faint bg-gilt-sunken border border-gilt-hairline px-3 py-1.5 rounded-full">
                                                        Client ID: {chat.client_id.slice(0, 8)}...
                                                    </span>
                                                )}
                                                {chat.lockr_id && (
                                                    <span className="text-xs font-mono tabular-nums text-gilt-faint bg-gilt-sunken border border-gilt-hairline px-3 py-1.5 rounded-full">
                                                        Lockr ID: {chat.lockr_id.slice(0, 8)}...
                                                    </span>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))
                    ) : (
                        <div className="text-center py-16 bg-gilt-card border border-gilt-hairline rounded-xl">
                            <MessageCircleMore className="w-16 h-16 text-gilt-brass mx-auto mb-4" />
                            <h3 className="text-lg font-medium text-gilt-ink mb-2">No chat history found</h3>
                            <p className="text-gilt-soft">Start a new conversation to see it here</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
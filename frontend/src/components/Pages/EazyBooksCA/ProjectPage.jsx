import {useState, useCallback, useEffect} from "react";
import {
    Plus,
    Users,
    History,
    MessageSquare,
    MessageCircleMore,
    Table,
    FileText,
    FolderPlus,
    ArrowLeft,
    X,
    Loader2,
    Download,
    Maximize2,
    Minimize2,
    ZoomIn,
    ZoomOut,
    RotateCw,
    CloudUpload
} from "lucide-react";
import ChatbotLight from "../ChatBot/ChatbotLight"
import ClientFilesTable from './ClientFilesTable';
import { chatApi, clientsApi, filesApi } from "../../../services/api";

export default function ProjectPage({project: initialProject, onBack, onFileDelete}) {
    // All hooks must be at the top
    const [project, setProject] = useState(initialProject);
    const [hovered, setHovered] = useState(null);
    const [newChat, setNewChat] = useState(false);
    const [viewedFile, setViewedFile] = useState(null);
    const [error, setError] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [zoom, setZoom] = useState(100);
    const [rotation, setRotation] = useState(0);
    const [previousMessages, setPreviousMessages] = useState([]);
    const [selectedChatId, setSelectedChatId] = useState(null);
    const [showChatbotLight, setShowChatbotLight] = useState(false);
    const [selectedChats, setSelectedChats] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [uploadError, setUploadError] = useState(null);
    const [isContextRequired, setIsContextRequired] = useState(false);

    // Update project state when initialProject changes
    useEffect(() => {
        setProject(initialProject);
    }, [initialProject]);

    // Handle selecting/deselecting all chats
    const handleSelectAll = (isChecked) => {
        if (isChecked) {
            const allChatIds = project.chats.map((chat) => chat.chat_id);
            setSelectedChats(allChatIds);
        } else {
            setSelectedChats([]);
        }
    };

    // Handle selecting/deselecting a single chat
    const handleSelectChat = (chatId, isChecked) => {
        if (isChecked) {
            setSelectedChats([...selectedChats, chatId]);
        } else {
            setSelectedChats(selectedChats.filter((id) => id !== chatId));
        }
    };

    const handleChatClick = async (chatId) => {
        setSelectedChatId(chatId);
        setIsContextRequired(true);
        try {
            const data = await chatApi.getMessages(chatId);
            setPreviousMessages(data);
            setShowChatbotLight(true);
        } catch (err) {
            console.error('Failed to fetch messages:', err);
        }
    };

    // Debug logging
    useEffect(() => {
        console.log('Project data:', project);
    }, [project]);

    const handleViewFile = useCallback(async (file) => {
        setError(null);
        setIsLoading(true);
        setViewedFile({...file, viewUrl: null}); // Set viewedFile immediately with null URL

        try {
            const url = file.file_link;
            if (url) {
                setViewedFile({...file, viewUrl: url});
            }
        } catch (error) {
            setError('Failed to load file preview');
            setViewedFile(null); // Close modal on error
            setIsLoading(false);
        }
    }, []);

    const handleFullscreenToggle = useCallback(() => {
        setIsFullscreen(!isFullscreen);
    }, [isFullscreen]);

    const handleZoomIn = useCallback(() => {
        setZoom(prev => Math.min(prev + 25, 200));
    }, []);

    const handleZoomOut = useCallback(() => {
        setZoom(prev => Math.max(prev - 25, 50));
    }, []);

    const handleRotate = useCallback(() => {
        setRotation(prev => (prev + 90) % 360);
    }, []);

    const handleCloseModal = useCallback(() => {
        setViewedFile(null);
        setIsLoading(false);
        setIsFullscreen(false);
    }, []);

    const handleStartNewChat = useCallback(async () => {
        setNewChat(true);
        setShowChatbotLight(true); // Show ChatbotLight when starting a new chat

        // Clear both lockers and clients cache
        localStorage.removeItem(`lockers`);
        localStorage.removeItem(`lockers_timestamp`);
        localStorage.removeItem(`clients`);
        localStorage.removeItem(`clients_timestamp`);

        // Refresh both lockers and clients data
        try {
            await Promise.all([
                clientsApi.getLockersWithDetails(),
                clientsApi.getClientsWithDetails()
            ]);
        } catch (err) {
            console.error("Error refreshing data:", err);
        }
    }, []);

    const handleFileDelete = (fileId) => {
        setProject(prev => ({
            ...prev,
            files: prev.files.filter(file => file.file_id !== fileId)
        }));
        // Call the parent's onFileDelete callback
        if (onFileDelete) {
            onFileDelete(fileId);
        }
    };

    const handleRefresh = async () => {
        try {
            const data = await clientsApi.getLockersWithDetails();

            // Find and update the current project in the response
            const updatedProject = data.lockers.find(p => p.lockr_id === project.lockr_id);
            if (updatedProject) {
                setProject(updatedProject);
            }
        } catch (err) {
            console.error("Error refreshing project data:", err);
        }
    };

    const handleFileUpload = async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        setUploading(true);
        setUploadError(null);

        try {
            const formData = new FormData();
            const entityIdKey = 'lockr_id';  // Define the entity ID key
            formData.append(entityIdKey, project.lockr_id);  // Use lockr_id instead of id
            files.forEach((file) => {
                formData.append('files', file);
            });

            await filesApi.upload(formData);

            // Clear both lockers and clients cache
            localStorage.removeItem(`lockers`);
            localStorage.removeItem(`lockers_timestamp`);
            localStorage.removeItem(`clients`);
            localStorage.removeItem(`clients_timestamp`);

            // Refresh both lockers and clients data
            try {
                const [lockersData, clientsData] = await Promise.all([
                    clientsApi.getLockersWithDetails(),
                    clientsApi.getClientsWithDetails()
                ]);

                // Update the project state with the latest data
                const updatedProject = lockersData.lockers.find(p => p.lockr_id === project.lockr_id);
                if (updatedProject) {
                    setProject(updatedProject);
                }
            } catch (err) {
                console.error("Error refreshing data:", err);
            }
        } catch (err) {
            console.error('Error uploading files:', err);
            setUploadError(err.message || 'Failed to upload files');
        } finally {
            setUploading(false);
        }
    };

    // Now we can have conditional returns
    // If newChat or showChatbotLight is true, render ChatbotLight
    if (newChat || showChatbotLight) {
        return <ChatbotLight 
            vault={project} 
            previousMessages={previousMessages || {messages: []}}
            chatId={selectedChatId}
            isContextRequired = {isContextRequired}
            onBack={() => {
                setNewChat(false);
                setShowChatbotLight(false);
                setPreviousMessages(null);
                setSelectedChatId(null);
                setIsContextRequired(false);
            }}
        />;
    }

    return (
        <div className="p-6 max-w-5xl mx-auto">
            {/* Error Message */}
            {error && (
                <div className="mb-4 p-4 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit rounded-lg">
                    {error}
                </div>
            )}
            {uploadError && (
                <div className="mb-4 p-4 bg-gilt-sunken border border-gilt-debit/40 text-gilt-debit rounded-lg">
                    {uploadError}
                </div>
            )}

            {/* Back Button */}
            <div className="mb-4">
                <button onClick={onBack} className="flex items-center text-gilt-soft hover:text-gilt-ink transition-colors duration-150">
                    <ArrowLeft size={20} className="mr-2"/>
                </button>
            </div>

            {/* Header Section */}
            <div className="mb-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="gl-display text-3xl text-gilt-ink">{project.name}</h1>
                        <p className="text-sm text-gilt-soft">
                            <span className="font-mono tabular-nums">{Array.isArray(project.files) ? project.files.length : 0}</span> files • <span className="font-mono tabular-nums">{project.size || "Unknown"}</span> MB
                        </p>
                    </div>
                    <div className="flex items-center space-x-4">
                        <input
                            type="file"
                            multiple
                            accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg,.tiff,.ico"
                            onChange={handleFileUpload}
                            className="hidden"
                            id="file-upload"
                        />
                        <label
                            htmlFor="file-upload"
                            className="flex items-center px-4 py-2 text-sm font-medium text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass transition-colors cursor-pointer"
                        >
                            <CloudUpload className="h-4 w-4 mr-2" />
                            Upload Files
                        </label>
                    </div>
                </div>
            </div>

            {/* Create New Query */}
            <div className="grid grid-cols-4 gap-4 mb-6">
                <div
                    className="border border-gilt-hairline bg-gilt-card rounded-lg p-6 flex items-center justify-center text-gilt-soft cursor-pointer hover:border-gilt-brass transition-colors duration-150">
                    <button onClick={handleStartNewChat} className="flex items-center">
                        <Plus size={20} className="text-gilt-brass mr-3" />
                        <span className="text-gilt-soft">Start a query from scratch</span>
                    </button>
                </div>
            </div>

            {/* Recent Chats */}
            <h2 className="text-lg font-semibold mb-4 text-gilt-ink">Recent Chats</h2>
            <div className="overflow-auto h-[200px] border border-gilt-hairline bg-gilt-card rounded-lg mb-6">
                <table className="w-full">
                    <thead className="border-b border-gilt-hairline">
                    <tr>
                        <th className="p-3 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft text-left">
                            <input
                                type="checkbox"
                                className="form-checkbox h-4 w-4 text-gilt-brass border-gilt-hairline rounded focus:ring-gilt-brass"
                                onChange={(e) => handleSelectAll(e.target.checked)}
                            />
                        </th>
                        <th className="p-3 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft text-left">Chat Name</th>
                        <th className="p-3 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft text-left">Date</th>
                        <th className="p-3 font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft text-left">Action</th>
                    </tr>
                    </thead>
                    <tbody className="divide-y divide-gilt-hairline">
                    {project.chats && project.chats.length > 0 ? (
                        project.chats.map((chat) => (
                            <tr key={chat.chat_id} className="hover:bg-gilt-sunken transition-colors duration-150">
                                {/* Checkbox */}
                                <td className="p-3">
                                    <input
                                        type="checkbox"
                                        className="form-checkbox h-4 w-4 text-gilt-brass border-gilt-hairline rounded focus:ring-gilt-brass"
                                        checked={selectedChats.includes(chat.chat_id)}
                                        onChange={(e) => handleSelectChat(chat.chat_id, e.target.checked)}
                                    />
                                </td>
                                {/* Chat Name with Icon */}
                                <td className="p-3 text-sm text-gilt-ink">
                                    <div className="flex items-center gap-2">
                                        <MessageCircleMore className="h-4 w-4 text-gilt-faint" />
                                        <span>{chat.chat_name || "Unnamed Chat"}</span>
                                    </div>
                                </td>
                                {/* Date */}
                                <td className="p-3 text-sm font-mono tabular-nums text-gilt-soft">
                                    {new Date(chat.chat_date).toLocaleDateString()}
                                </td>
                                {/* Continue Chat Button */}
                                <td className="p-3">
                                    <button
                                        onClick={() => handleChatClick(chat.chat_id)}
                                        className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gilt-soft bg-gilt-card border border-gilt-hairline rounded-lg hover:border-gilt-brass hover:text-gilt-brass focus:outline-none focus:ring-2 focus:ring-gilt-brass focus:ring-offset-2 transition-colors duration-150"
                                    >
                                        <MessageSquare className="h-4 w-4"/>
                                        Continue Chat
                                    </button>
                                </td>
                            </tr>
                        ))
                    ) : (
                        <tr>
                            <td colSpan="4" className="p-3 text-center text-sm text-gilt-soft">
                                No recent chats.
                            </td>
                        </tr>
                    )}
                    </tbody>
                </table>
            </div>

            <ClientFilesTable 
                files={project.files} 
                onFileClick={handleViewFile}
                onFileDelete={handleFileDelete}
                onRefresh={handleRefresh}
            />

            {/* Enhanced File Preview Modal */
            }
            {
                viewedFile && (
                    <div
                        className="fixed inset-0 bg-gilt-ink/40 flex items-center justify-center z-50 transition-opacity duration-300">
                        <div
                            className={`bg-gilt-card border border-gilt-hairline rounded-xl relative flex flex-col transition-all duration-300 ease-in-out ${
                                isFullscreen ? 'w-full h-full rounded-none' : 'max-w-5xl w-full h-[85vh] m-4'
                            }`}>
                            {/* Header */}
                            <div className="flex items-center justify-between px-6 py-4 border-b border-gilt-hairline">
                                <div className="flex items-center space-x-4">
                                    <div className="flex flex-col">
                                        <h2 className="text-xl font-semibold text-gilt-ink">{viewedFile.file_name}</h2>
                                        <p className="text-sm text-gilt-soft">
                                            {viewedFile.metadata?.content_type || 'Document'}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center space-x-2">
                                    <button
                                        onClick={() => window.open(viewedFile.viewUrl, '_blank')}
                                        className="p-2 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors"
                                        title="Download"
                                    >
                                        <Download className="h-5 w-5"/>
                                    </button>
                                    <button
                                        onClick={handleFullscreenToggle}
                                        className="p-2 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors"
                                        title={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
                                    >
                                        {isFullscreen ? <Minimize2 className="h-5 w-5"/> : <Maximize2 className="h-5 w-5"/>}
                                    </button>
                                    <button
                                        onClick={handleCloseModal}
                                        className="p-2 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors"
                                        title="Close"
                                    >
                                        <X className="h-5 w-5"/>
                                    </button>
                                </div>
                            </div>

                            {/* Content Area */}
                            <div className="flex-1 relative overflow-hidden group">
                                {/* Toolbar */}
                                <div
                                    className="absolute top-2 left-1/2 transform -translate-x-1/2 flex items-center space-x-2 bg-gilt-card border border-gilt-hairline px-4 py-2 rounded-full shadow-sm z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none group-hover:pointer-events-auto">
                                    <button
                                        onClick={handleZoomOut}
                                        className="p-1.5 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                        title="Zoom Out"
                                        disabled={zoom <= 50}
                                    >
                                        <ZoomOut className="h-4 w-4"/>
                                    </button>
                                    <span className="text-sm font-medium font-mono tabular-nums text-gilt-ink min-w-[3rem] text-center">
                                        {zoom}%
                                    </span>
                                    <button
                                        onClick={handleZoomIn}
                                        className="p-1.5 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                        title="Zoom In"
                                        disabled={zoom >= 200}
                                    >
                                        <ZoomIn className="h-4 w-4"/>
                                    </button>
                                    <div className="w-px h-4 bg-gilt-hairline mx-2"></div>
                                    <button
                                        onClick={handleRotate}
                                        className="p-1.5 text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink rounded-full transition-colors"
                                        title="Rotate"
                                    >
                                        <RotateCw className="h-4 w-4"/>
                                    </button>
                                </div>

                                {isLoading && (
                                    <div
                                        className="absolute inset-0 flex flex-col items-center justify-center bg-gilt-card z-10">
                                        <Loader2 className="h-8 w-8 animate-spin text-gilt-brass mb-2"/>
                                        <span className="text-sm font-medium text-gilt-soft">Loading document...</span>
                                    </div>
                                )}

                                <div className="absolute inset-0 p-4 overflow-auto">
                                    <div
                                        className="w-full h-full flex items-center justify-center"
                                        style={{
                                            transform: `scale(${zoom / 100}) rotate(${rotation}deg)`,
                                            transformOrigin: 'center center',
                                            transition: 'transform 0.3s ease'
                                        }}
                                    >
                                        <iframe
                                            src={viewedFile.viewUrl || ''}
                                            className="w-full h-full border border-gilt-hairline bg-gilt-card rounded-lg"
                                            title={viewedFile.file_name}
                                            onLoad={() => setIsLoading(false)}
                                        ></iframe>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                )
            }
        </div>
    );
}
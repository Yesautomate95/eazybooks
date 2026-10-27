import { useState } from "react";
import { CloudUpload, FileText, User, Upload, CheckCircle2, XCircle } from "lucide-react";
import { clientsApi, filesApi } from "../../../services/api";

export default function Cloud() {
    const [selectedFiles, setSelectedFiles] = useState([]);
    const [destinationType, setDestinationType] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(false);

    const handleFileSelect = (e) => {
        const files = Array.from(e.target.files);
        setSelectedFiles(files);
        setError(null);
        setSuccess(false);
    };

    const refreshData = async () => {
        try {
            // Refresh lockers data
            const lockersResult = await clientsApi.getLockersWithDetails();
            localStorage.setItem('lockers', JSON.stringify(lockersResult.lockers));
            localStorage.setItem('lockers_timestamp', Date.now().toString());

            // Refresh clients data
            const clientsResult = await clientsApi.getClientsWithDetails();
            localStorage.setItem('clients', JSON.stringify(clientsResult.clients));
            localStorage.setItem('clients_timestamp', Date.now().toString());
        } catch (err) {
            console.error("Error refreshing data:", err);
        }
    };

    const handleUpload = async () => {
        if (!destinationType) {
            setError("Please select a destination type (Locker or Client)");
            return;
        }

        if (selectedFiles.length === 0) {
            setError("Please select files to upload");
            return;
        }

        setUploading(true);
        setError(null);
        setSuccess(false);

        try {
            const formData = new FormData();
            formData.append('destination_type', destinationType);

            // Append each file
            selectedFiles.forEach((file) => {
                formData.append('files', file);
            });

            await filesApi.smartUpload(formData);

            // Refresh data after successful upload
            await refreshData();

            setSuccess(true);
            setSelectedFiles([]);
            setDestinationType(null);
        } catch (err) {
            console.error("Error uploading files:", err);
            setError(err.response?.data?.message || "Failed to upload files. Please try again.");
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="min-h-screen">
            <div className="max-w-4xl mx-auto px-6 py-8">
                {/* Header Section */}
                <div className="mb-10 text-center">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-gilt-sunken border border-gilt-hairline mb-4">
                        <CloudUpload className="w-7 h-7 text-gilt-brass" />
                    </div>
                    <h1 className="gl-display text-3xl text-gilt-ink mb-2">Cloud Upload</h1>
                    <p className="text-gilt-soft text-base max-w-md mx-auto">
                        Upload files to lockers or clients with our secure and efficient system
                    </p>
                </div>

                {/* Main Content */}
                <div className="bg-gilt-card rounded-xl border border-gilt-hairline p-8">
                    {/* Destination Type Selection */}
                    <div className="mb-8">
                        <h2 className="text-lg font-medium text-gilt-ink mb-6">Select Destination</h2>
                        <div className="grid grid-cols-2 gap-4">
                            <button
                                onClick={() => setDestinationType("LOCKR")}
                                className={`group flex items-center justify-center gap-3 p-6 rounded-lg border transition-colors duration-150 ${
                                    destinationType === "LOCKR"
                                        ? "bg-gilt-sunken border-gilt-brass text-gilt-ink"
                                        : "bg-gilt-card border-gilt-hairline text-gilt-soft hover:border-gilt-brass"
                                }`}
                            >
                                <div className={`p-2.5 rounded-lg bg-gilt-sunken border transition-colors duration-150 ${
                                    destinationType === "LOCKR" ? "border-gilt-brass/40" : "border-gilt-hairline"
                                }`}>
                                    <FileText size={20} className="text-gilt-brass" />
                                </div>
                                <div className="text-left">
                                    <span aria-hidden="true" className="block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass">№ 01</span>
                                    <span className="block font-medium text-base text-gilt-ink">Locker</span>
                                    <span className="block text-sm text-gilt-soft">Upload to a project locker</span>
                                </div>
                            </button>
                            <button
                                onClick={() => setDestinationType("CLIENT")}
                                className={`group flex items-center justify-center gap-3 p-6 rounded-lg border transition-colors duration-150 ${
                                    destinationType === "CLIENT"
                                        ? "bg-gilt-sunken border-gilt-brass text-gilt-ink"
                                        : "bg-gilt-card border-gilt-hairline text-gilt-soft hover:border-gilt-brass"
                                }`}
                            >
                                <div className={`p-2.5 rounded-lg bg-gilt-sunken border transition-colors duration-150 ${
                                    destinationType === "CLIENT" ? "border-gilt-brass/40" : "border-gilt-hairline"
                                }`}>
                                    <User size={20} className="text-gilt-brass" />
                                </div>
                                <div className="text-left">
                                    <span aria-hidden="true" className="block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-brass">№ 02</span>
                                    <span className="block font-medium text-base text-gilt-ink">Client</span>
                                    <span className="block text-sm text-gilt-soft">Upload to a client folder</span>
                                </div>
                            </button>
                        </div>
                    </div>

                    {/* File Upload Section */}
                    <div className="mb-8">
                        <h2 className="text-lg font-medium text-gilt-ink mb-6">Select Files</h2>
                        <div
                            className={`border border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors duration-150 ${
                                selectedFiles.length > 0
                                    ? "border-gilt-credit/50 bg-gilt-sunken"
                                    : "border-gilt-hairline hover:border-gilt-brass"
                            }`}
                            onClick={() => document.getElementById('file-input').click()}
                        >
                            <input
                                type="file"
                                id="file-input"
                                multiple
                                className="hidden"
                                onChange={handleFileSelect}
                            />
                            <div className="flex flex-col items-center">
                                <div className={`p-4 rounded-xl mb-6 bg-gilt-sunken border transition-colors duration-150 ${
                                    selectedFiles.length > 0 ? "border-gilt-credit/40" : "border-gilt-hairline"
                                }`}>
                                    <CloudUpload className={`${selectedFiles.length > 0 ? "text-gilt-credit" : "text-gilt-faint"}`} size={32} />
                                </div>
                                <p className="text-gilt-ink font-medium text-base mb-2">
                                    {selectedFiles.length > 0
                                        ? `${selectedFiles.length} file(s) selected`
                                        : "Click to select files"}
                                </p>
                                <p className="text-sm text-gilt-soft">
                                    Drag and drop files here or click to browse
                                </p>
                            </div>
                        </div>
                    </div>

                    {/* Error Message */}
                    {error && (
                        <div className="mb-6 p-4 bg-gilt-sunken text-gilt-debit rounded-lg border border-gilt-debit/40 flex items-center gap-3">
                            <XCircle size={20} />
                            <span>{error}</span>
                        </div>
                    )}

                    {/* Success Message */}
                    {success && (
                        <div className="mb-6 p-4 bg-gilt-sunken text-gilt-credit rounded-lg border border-gilt-credit/40 flex items-center gap-3">
                            <CheckCircle2 size={20} />
                            <span>Files uploaded successfully!</span>
                        </div>
                    )}

                    {/* Upload Button */}
                    <button
                        onClick={handleUpload}
                        disabled={uploading || selectedFiles.length === 0 || !destinationType}
                        className={`w-full py-4 px-6 rounded-md font-medium transition-colors duration-150 flex items-center justify-center gap-3 ${
                            uploading || selectedFiles.length === 0 || !destinationType
                                ? "bg-gilt-sunken text-gilt-faint cursor-not-allowed"
                                : "bg-gilt-brass text-gilt-paper hover:bg-gilt-brass/90"
                        }`}
                    >
                        {uploading ? (
                            <>
                                <div className="w-5 h-5 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
                                <span className="text-base">Uploading...</span>
                            </>
                        ) : (
                            <>
                                <Upload size={20} />
                                <span className="text-base">Upload Files</span>
                            </>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
} 
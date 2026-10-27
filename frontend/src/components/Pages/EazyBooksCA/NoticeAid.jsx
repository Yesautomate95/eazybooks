import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CloudUpload, X, FileText } from "lucide-react";
import { filesApi } from "../../../services/api";

export default function NoticeAid() {
    const navigate = useNavigate();
    const user_id = "b20ac37e-8eef-481e-be3b-6b179672302b";
    const [files, setFiles] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const handleFileChange = (e) => {
        const newFiles = Array.from(e.target.files);
        setFiles(newFiles);
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setFiles(Array.from(e.dataTransfer.files));
    };

    const handleDragOver = (e) => {
        e.preventDefault();
    };

    const handleSubmit = async () => {
        if (files.length === 0) {
            alert("Please upload your notice file.");
            return;
        }

        setLoading(true);
        setError(null);

        try {
            // First create a client for notices (or use an existing one)
            // Using a fixed client ID for notices
            const client_id = "c1bec09d-02c7-41c9-aa9e-776d6e2e743e";

            const formData = new FormData();
            formData.append("user_id", user_id);
            console.log("client_id in formData:", client_id);
            console.log("user_id in formData:", user_id);
            files.forEach((file) => {
                formData.append("files", file);
            });

            await filesApi.uploadDb(formData);

            await new Promise(resolve => setTimeout(resolve, 1000));
            navigate("/chatbot");
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex flex-col items-center justify-center p-6">
            <div className="w-full max-w-2xl bg-gilt-card rounded-xl p-8 border border-gilt-hairline">
                <div className="flex items-center space-x-3 mb-6">
                    <FileText className="h-8 w-8 text-gilt-brass" />
                    <div>
                        <h1 className="gl-display text-2xl text-gilt-ink">Notice Aid</h1>
                        <p className="text-sm text-gilt-soft">Upload your Income tax or GST notice for analysis</p>
                    </div>
                </div>

                <div
                    className="flex flex-col items-center justify-center border border-dashed border-gilt-hairline rounded-xl p-12 text-center bg-gilt-sunken/50 cursor-pointer transition-colors duration-200 hover:border-gilt-brass group"
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                >
                    <div className="bg-gilt-sunken border border-gilt-hairline p-4 rounded-full mb-6 group-hover:border-gilt-brass/40 transition-colors duration-200">
                        <CloudUpload className="h-12 w-12 text-gilt-faint group-hover:text-gilt-brass transition-colors duration-200" />
                    </div>
                    <div className="space-y-2">
                        <p className="text-lg font-medium text-gilt-ink">Upload your notice</p>
                        <p className="text-sm text-gilt-soft">or</p>
                        <input
                            type="file"
                            accept=".pdf"
                            onChange={handleFileChange}
                            className="hidden"
                            id="file-upload"
                        />
                        <label htmlFor="file-upload" className="inline-block px-4 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors cursor-pointer">
                            Browse PDF
                        </label>
                        <p className="text-xs text-gilt-faint mt-2">Supported format: PDF</p>
                    </div>
                    {files.length > 0 && (
                        <div className="mt-8 w-full">
                            <div className="flex items-center justify-between mb-3">
                                <p className="text-sm font-medium text-gilt-soft">Selected notice (<span className="font-mono tabular-nums">{files.length}</span>)</p>
                                <button
                                    onClick={() => setFiles([])}
                                    className="text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                                >
                                    Clear
                                </button>
                            </div>
                            <ul className="space-y-2">
                                {files.map((file, index) => (
                                    <li key={index} className="flex items-center justify-between text-sm bg-gilt-card p-3 rounded-lg border border-gilt-hairline hover:border-gilt-brass transition-colors duration-150">
                                        <div className="flex items-center space-x-3 min-w-0">
                                            <div className="flex-shrink-0 w-8 h-8 bg-gilt-sunken border border-gilt-hairline rounded-lg flex items-center justify-center">
                                                <span className="font-mono text-[10px] uppercase text-gilt-brass">PDF</span>
                                            </div>
                                            <span className="truncate text-gilt-ink">{file.name}</span>
                                        </div>
                                        <button
                                            onClick={() => setFiles(files.filter((_, i) => i !== index))}
                                            className="p-1 hover:bg-gilt-sunken rounded-full transition-colors flex-shrink-0"
                                        >
                                            <X className="h-4 w-4 text-gilt-soft" />
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>

                <div className="flex justify-end mt-8">
                    <button
                        onClick={handleSubmit}
                        disabled={loading || files.length === 0}
                        className="px-6 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:bg-gilt-brass/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {loading ? "Processing..." : "Analyze Notice"}
                    </button>
                </div>

                {error && (
                    <div className="mt-6 text-center text-sm text-gilt-debit">
                        Error: {error}
                    </div>
                )}
            </div>
        </div>
    );
} 
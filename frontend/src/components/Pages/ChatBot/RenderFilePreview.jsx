import React from 'react'

const RenderFilePreview = ({file, setUploadedFile}) => {

    if (!file) return null;
    const fileType = file.type;

    if (fileType.startsWith("image/")) {
        return (
            <div className="flex items-center space-x-4 mt-4">
                <img
                    src={URL.createObjectURL(file)}
                    alt="Uploaded preview"
                    className="w-32 h-32 object-cover rounded-md shadow-md"
                />
            </div>
        );
    } else {
        return (
            <div className="flex items-center space-x-4 mt-4">
                <div className="p-2 border border-gray-300 rounded-md bg-gray-100">
                    <p className="text-sm text-gray-600 truncate">{file.name}</p>
                </div>
            </div>
        );
    }
}

export default RenderFilePreview;

import React from "react";
import {
  LucideFileText,
  LucideBanknote,
  LucideScale,
  LucideTrendingUp,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

function Advisor() {
  const navigate = useNavigate();
  
  const options = [
    {
      name: "GST Consultant",
      description: "Get expert guidance on all GST-related queries and compliance.",
      icon: <LucideFileText size={28} />,
    },
    {
      name: "Income Tax Advisor",
      description: "Navigate income tax regulations, deductions, and filings effortlessly.",
      icon: <LucideBanknote size={28} />,
    },
    {
      name: "Legal Consultant",
      description: "Resolve business and financial legal matters with AI-powered assistance.",
      icon: <LucideScale size={28} />,
    },
    {
      name: "Wealth & Investment Planner",
      description: "Receive tailored financial strategies for investments, savings, and wealth creation.",
      icon: <LucideTrendingUp size={28} />,
    },
  ];

  const handleOptionClick = () => {
    navigate('/chatbot');
  };

  return (
    <div className="h-screen flex flex-col px-6 md:px-12 bg-gray-50">
      {/* Heading Section (Top 30%) */}
      <div className="h-[30%] flex items-center justify-center text-center">
        <h1 className="text-2xl md:text-4xl font-bold text-gray-900 max-w-2xl">
          Which financial expert do you need today?
        </h1>
      </div>

      {/* Options Grid (Bottom 70%) */}
      <div className="h-[70%] grid grid-cols-1 md:grid-cols-2 gap-6 p-4 md:p-6">
        {options.map((option) => (
          <div
            key={option.name}
            onClick={handleOptionClick}
            className="flex items-center p-5 md:p-6 bg-white border border-gray-300 rounded-xl shadow-md transition-all hover:shadow-lg hover:scale-105 cursor-pointer"
          >
            <div className="p-3 md:p-4 bg-gray-100 rounded-xl text-gray-700">{option.icon}</div>
            <div className="ml-4">
              <h2 className="text-lg md:text-xl font-semibold text-gray-900">{option.name}</h2>
              <p className="text-sm md:text-base text-gray-600">{option.description}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default Advisor;

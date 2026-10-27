import React, { useState } from "react";
import Sidebar from "./Sidebar";
import ChatbotLight from "../ChatBot/ChatbotLight" // Import Lockr component
import Lockr from "./Lockr"
import Clients from "./Clients"
import Advisor from "./Advisor";
import History from "./History";
import Tally from "./Tally";
import TallyChat from "./TallyChat";

function ChatbotCA() {
    const [selectedComponent, setSelectedComponent] = useState(null);

    const renderComponent = () => {
    switch (selectedComponent) {
      case "Lockr":
       return <Lockr />;
      case "Advisor":
       return <Advisor />;
      case "Clients":
        return <Clients />;
      case "History":
        return <History />;
      case "Notice Aid":
        return <div className="p-6">Notice Aid Component</div>;
      case "Tally":
        return <Tally />;
      case "Tally Chat":
        return <TallyChat />;
      default:
        return <div className="p-6">Select a section</div>;
    }
  };

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <Sidebar setSelectedComponent={setSelectedComponent} />

      {/* Right Section (Dynamically Rendered) */}
      <div className="flex-1 bg-gray-100"><ChatbotLight /></div>
    </div>
  );
}

export default ChatbotCA;

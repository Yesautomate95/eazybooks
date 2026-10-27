import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import "katex/dist/katex.min.css"; // Importing the CSS for KaTeX
import { InlineMath, BlockMath } from "react-katex";
import { prompts } from "../../../data/ChatPrompts";
import { PulseLoader } from "react-spinners";
import { TypeAnimation } from "react-type-animation";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { MathJaxContext, MathJax } from "better-react-mathjax";
import RenderFilePreview from "./RenderFilePreview";
import logo from "../../../assets/logo black.png";
import RenderMessageContent from "./RenderMessageContent";
import TextareaAutosize from "react-textarea-autosize";
import { LucideFileText, LucideBanknote, LucideScale, LucideTrendingUp, ArrowLeft } from 'lucide-react';
import { chatApi } from "../../../services/api";
import { getToken } from "../../../services/auth";

const chatbotTypes = [
  {
    id: "gst",
    name: "GST Consultant",
    description: "Expert guidance on GST rates, returns & compliance",
    icon: <LucideFileText size={20} />,
    trigger: "@gst"
  },
  {
    id: "tax",
    name: "Income Tax Advisor",
    description: "Tax planning, deductions & ITR filing help",
    icon: <LucideBanknote size={20} />,
    trigger: "@tax"
  },
  {
    id: "legal",
    name: "Legal Consultant",
    description: "Business laws, contracts & compliance advice",
    icon: <LucideScale size={20} />,
    trigger: "@legal"
  },
  {
    id: "wealth",
    name: "Wealth & Investment Planner",
    description: "Investment planning & wealth management",
    icon: <LucideTrendingUp size={20} />,
    trigger: "@wealth"
  }
];

const ChatbotLight = ({ client, vault, previousMessages, chatId = null, onBack, isContextRequired = false}) => {
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const chatEndRef = useRef(null);
  const [displayedPrompts, setDisplayedPrompts] = useState([]);
  const [uploadedFile, setUploadedFile] = useState(null);
  const fileInputRef = useRef(null);
  const [selectedChatbot, setSelectedChatbot] = useState(null);
  const [showChatbotMenu, setShowChatbotMenu] = useState(false);
  const [mentionSearch, setMentionSearch] = useState("");
  const menuRef = useRef(null);
  const [dropdownPosition, setDropdownPosition] = useState('bottom');
  const [isChatContextRequired, setIsChatContextRequired] = useState(isContextRequired);
  const [isThinking, setIsThinking] = useState(false);
  const [isThinkingVisible, setIsThinkingVisible] = useState(true);

const [messages, setMessages] = useState(
  previousMessages?.messages
    ? previousMessages.messages.map((msg) => ({
        sender: msg.role === "user" ? "user" : "bot",
        text: msg.content,
        timestamp: msg.timestamp,
      }))
    : []
);

const toggleThinking = (index) => {
  setMessages(prevMessages =>
    prevMessages.map((message, i) =>
      i === index
        ? { ...message, isThinkingVisible: !message.isThinkingVisible }
        : message
    )
  );
};

  useEffect(() => {
    refreshPrompts();
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const storedPrompt = localStorage.getItem("portfolioPrompt");
    if (storedPrompt) {
      setMessages((prevMessages) => [
        ...prevMessages,
        { sender: "bot", text: storedPrompt },
      ]);
      localStorage.removeItem("portfolioPrompt");
    }
  }, []);

  useEffect(() => {
    const storedPrompt = localStorage.getItem("userPrompt");
    if (storedPrompt) {
      sendMessage(storedPrompt);
      localStorage.removeItem("userPrompt");
    }
  }, []);

  useEffect(() => {
    const generateChatId = () => {
      return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
        (
          +c ^
          (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))
        ).toString(16)
      );
    };

    const newChatId = chatId || generateChatId();
    sessionStorage.setItem("chat_id", newChatId);
  }, []);

  useEffect(() => {
    if (isTyping) {
      // Scroll to bottom when typing
      chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [isTyping]);

  // Add useEffect for dropdown positioning
  useEffect(() => {
    if (showChatbotMenu && menuRef.current) {
      const buttonRect = menuRef.current.getBoundingClientRect();
      const windowHeight = window.innerHeight;
      const dropdownHeight = 400; // Approximate height of dropdown

      // Check if there's enough space below
      const spaceBelow = windowHeight - buttonRect.bottom;
      // Check if there's enough space above
      const spaceAbove = buttonRect.top;

      if (spaceBelow >= dropdownHeight) {
        setDropdownPosition('bottom');
      } else if (spaceAbove >= dropdownHeight) {
        setDropdownPosition('top');
      } else {
        // If neither top nor bottom has enough space, position where there's more space
        setDropdownPosition(spaceAbove > spaceBelow ? 'top' : 'bottom');
      }
    }
  }, [showChatbotMenu]);

  // Handle clicks outside the chatbot menu
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setShowChatbotMenu(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Handle input changes and @ mentions
  const handleInputChange = (e) => {
    const value = e.target.value;
    setInput(value);

    // Check for @ symbol
    const lastAtIndex = value.lastIndexOf('@');
    if (lastAtIndex !== -1) {
      const searchText = value.slice(lastAtIndex + 1).toLowerCase().trim();
      setMentionSearch(searchText);
      setShowChatbotMenu(true);
    } else {
      setShowChatbotMenu(false);
      setMentionSearch("");
    }
  };

  // Filter chatbots based on mention search
  const filteredChatbots = chatbotTypes.filter(bot => {
    if (!mentionSearch) return true;
    
    const searchLower = mentionSearch.toLowerCase();
    return (
      bot.trigger.toLowerCase().replace('@', '').startsWith(searchLower) || // Match trigger without @ symbol
      bot.name.toLowerCase().split(' ')[0].startsWith(searchLower) // Match first word of name
    );
  });

  // Handle chatbot selection from dropdown or @ mention
  const handleChatbotSelect = (chatbot) => {
    // If clicking the same chatbot, deselect it
    if (selectedChatbot?.id === chatbot.id) {
      setSelectedChatbot(null);
    } else {
      setSelectedChatbot(chatbot);
    }
    setShowChatbotMenu(false);
    setMentionSearch("");
    
    // If there's an @ mention in the input, replace it with the chatbot trigger
    const lastAtIndex = input.lastIndexOf('@');
    if (lastAtIndex !== -1) {
      const beforeAt = input.slice(0, lastAtIndex);
      const afterAt = input.slice(lastAtIndex).split(' ')[1] || '';
      const newInput = beforeAt + (selectedChatbot?.id === chatbot.id ? '' : chatbot.trigger + ' ') + afterAt;
      setInput(newInput.trim() + ' ');
    }
  };

  // Modified sendMessage to handle chatbot types
  const sendMessage = async (message) => {
    if (!message.trim() && !uploadedFile) {
      console.error("Message or file required.");
      return;
    }

    // Check for chatbot trigger in message
    let activeChatbot = selectedChatbot;
    for (const bot of chatbotTypes) {
      if (message.includes(bot.trigger)) {
        activeChatbot = bot;
        message = message.replace(bot.trigger, '').trim();
        break;
      }
    }

    const userMessage = { 
      sender: "user", 
      text: message,
      chatbot: activeChatbot,
      file: uploadedFile // Include file in message object
    };
    
    setMessages((prevMessages) => [...prevMessages, userMessage]);
    setInput("");
    setIsTyping(true);

    const formData = new FormData();
    const token = getToken();
    
    // Only append message if it exists
    if (message.trim()) {
      formData.append("message", message);
    }
    
    // Always append file if it exists
    if (uploadedFile) {
      formData.append("file", uploadedFile);
    }

    try {
      function getJson(s) {
        s = s.slice(s.search(/[\[{]/));
        try {
          return JSON.parse(s);
        } catch (e) {
          return JSON.parse(s.slice(0, e.message.match(/position (\d+)/)[1]));
        }
      }

      let chatId = sessionStorage.getItem("chat_id");
      formData.append("chat_id", chatId);
      
      if (client) {
        formData.append("folder_id", client.client_id);
        formData.append("client_id", client.client_id);
      }
      if (vault) {
        formData.append("folder_id", vault.lockr_id);
        formData.append("lockr_id", vault.lockr_id);
      }
      if (activeChatbot) formData.append("chatbot_id", activeChatbot.id);
      formData.append("is_context_required", isChatContextRequired);
      setIsChatContextRequired(false);

      try {
        const response = await chatApi.streamChat(formData);
        setIsThinking(true);

        if (!response.body) {
          throw new Error("ReadableStream not supported");
        }

        setMessages(prev => [...prev, { 
          sender: "bot", 
          text: "",
          chatbot: activeChatbot 
        }]);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let done = false;
        let accumulatedMessage = "";
        let thoughts = []

        while (!done) {
          const { value, done: streamDone } = await reader.read();
          done = streamDone;
          const data = decoder.decode(value, { stream: true });
          const chunks = data.split("\n");

          for (const chunk of chunks) {
            try {
              if (!chunk.trim()) continue; // Skip empty chunks

              const parsedEvent = JSON.parse(chunk);
              // Extract event type
              const { event, data } = parsedEvent;
              const parsedData = JSON.parse(data);

              if (parsedData.type === 'thinking') {
                const thought = {
                  content: parsedData.content,
                  timestamp: new Date().toISOString()
                };

                thoughts.push(thought);

                setMessages(prevMessages => {
                  const updatedMessages = [...prevMessages];
                  const lastMessageIndex = updatedMessages.length - 1;
                  
                  if (lastMessageIndex >= 0) {
                    updatedMessages[lastMessageIndex].thinking = [thought];
                    updatedMessages[lastMessageIndex].isThinkingVisible = true;
                  }
                  
                  return updatedMessages;
                });
              }
              else if (parsedData.content) {
                accumulatedMessage += parsedData.content;
                setMessages(prevMessages => {
                  const updatedMessages = [...prevMessages];
                  const lastMessage = updatedMessages[updatedMessages.length - 1];
                  if (lastMessage && lastMessage.sender === "bot") {
                    lastMessage.text = accumulatedMessage;
                    lastMessage.thinking = [];
                  }
                  return updatedMessages;
                });
                if (accumulatedMessage.length > 0) {
                  setIsTyping(false);
                }
              }
            } catch (error) {
              console.error("Error parsing chunk:", error, chunk);
            }
          }
        }
      } catch (error) {
        console.error("Error sending message:", error);
      }
    } catch (error) {
      console.error("Error sending message:", error);
    } finally {
      setIsTyping(false);
      setUploadedFile(null);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === "Enter" && !isTyping) {
      sendMessage(input);
    }
  };

  const refreshPrompts = () => {
    const shuffledPrompts = [...prompts].sort(() => 0.5 - Math.random());
    setDisplayedPrompts(shuffledPrompts.slice(0, 4));
  };

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (file) {
      setUploadedFile(file);
    }
    fileInputRef.current.value = ""; // Reset file input
  };

  const handleAddAttachmentClick = () => {
    fileInputRef.current.click();
  };

  useEffect(() => {
    const handlePaste = (event) => {
      const clipboardItems = event.clipboardData.items;

      for (let item of clipboardItems) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) {
            setUploadedFile(file);
            break;
          }
        }
      }
    };

    document.addEventListener("paste", handlePaste);
    const storedContext = localStorage.getItem("chatContext");
      if (storedContext) {
        let contextMessage;
        try {
          // Try to parse in case we stored a JSON object
          const parsedContext = JSON.parse(storedContext);
          contextMessage = parsedContext.guidance || parsedContext;
        } catch (error) {
          // If parsing fails, treat it as a simple string
          contextMessage = storedContext;
        }

        // Add the context as an initial bot message
        setMessages((prevMessages) => [
          ...prevMessages,
          { sender: "bot", text: contextMessage }
        ]);

        // Remove the context so it's only used once
        localStorage.removeItem("chatContext");
      }

    return () => {
      document.removeEventListener("paste", handlePaste);
    };
  }, []);

  const getFileIcon = (fileType) => {
    if (fileType.startsWith('image/')) return 'image';
    if (fileType === 'application/pdf') return 'picture_as_pdf';
    if (fileType.includes('word') || fileType.includes('doc')) return 'description';
    if (fileType === 'text/plain') return 'text_snippet';
    return 'insert_drive_file';
  };

  const getFileColor = (fileType) => {
    if (fileType.startsWith('image/')) return 'text-gilt-brass';
    if (fileType === 'application/pdf') return 'text-gilt-debit';
    if (fileType.includes('word') || fileType.includes('doc')) return 'text-gilt-soft';
    if (fileType === 'text/plain') return 'text-gilt-soft';
    return 'text-gilt-faint';
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto text-gilt-ink pt-6">
      {/* Add Back Button */}
      {(client || vault || onBack) && (
        <div className="px-6 mb-4">
          <button 
            onClick={onBack} 
            className="flex items-center text-gilt-soft hover:text-gilt-ink transition-colors duration-150"
          >
            <ArrowLeft size={20} className="mr-2"/>
          </button>
        </div>
      )}

      <div
        className={`flex-1 flex flex-col items-center ${
          messages.length === 0 ? "justify-center" : "mt-10"
        }`}
      >
        <div
            className={`w-full max-w-4xl px-6 flex flex-col relative ${
                messages.length > 0 ? "pb-32" : ""
            }`}
        >
          {messages.length === 0 && (
              <div>
                <header className="mb-6">
                  <h1 className="gl-display text-2xl md:text-3xl text-left text-gilt-ink">
                    <TypeAnimation
                        sequence={["Hi there, "]}
                        wrapper="span"
                        className="text-gilt-ink"
                        cursor={false}
                        speed={50}
                    />
                    <TypeAnimation
                        sequence={[
                          650,
                          `${
                              localStorage.getItem("username")
                                  ? localStorage.getItem("username")
                                  : "Guest"
                          }`,
                        ]}
                        wrapper="span"
                        className="gl-wonk text-gilt-brass"
                        cursor={false}
                        speed={50}
                    />
                  </h1>
                  <p className="mt-2 text-sm md:text-md font-normal text-gilt-soft text-left">
                    What would you like to know?
                  </p>
                </header>
                <div className="my-2">
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 md:gap-4">
                    {displayedPrompts.map((item, index) => (
                        <button
                            key={index}
                            onClick={() => sendMessage(item.prompt)}
                            className="flex flex-col items-start gap-2 px-3 py-4 md:px-4 md:py-6 text-left bg-gilt-card border border-gilt-hairline rounded-lg hover:border-gilt-brass transition-colors duration-150"
                        >
                          <span aria-hidden="true" className="font-mono tabular-nums text-[10px] uppercase tracking-[0.14em] text-gilt-brass">
                            № {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="text-[13px] text-gilt-ink">{item.prompt}</span>
                        </button>
                    ))}
                  </div>
                </div>
              </div>
          )}
          {messages.length > 0 && (
            <div className="flex-1 p-0 sm:p-6 space-y-6 overflow-y-auto">
              {messages.map((message, index) => (
                <div
                  key={index}
                  className={`flex ${message.sender === "user" ? "justify-end" : "w-full m-0"}`}
                >
                  {message.sender === "user" ? (
                    <div className="max-w-[85%] pl-8 sm:pl-24">
                      <div className="bg-gilt-sunken border border-gilt-hairline rounded-xl px-5 py-3">
                        <div className="text-gilt-ink text-[15px] font-normal leading-relaxed">
                          {message.text}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="w-full space-y-4">
                      {message.thinking && message.thinking.length > 0 && !message.text && (
                        <div className="mb-4">
                          <div className="bg-gilt-card rounded-xl overflow-hidden border border-gilt-hairline">
                            <div
                              className="flex items-center px-5 py-4 cursor-pointer bg-gilt-sunken"
                              onClick={() => toggleThinking(index)}
                            >
                              <div className="flex items-center space-x-4">
                                <div className="relative">
                                  <div className="absolute inset-0 bg-gilt-brass rounded-full animate-ping opacity-30"/>
                                  <div className="relative w-6 h-6">
                                    <div className="absolute w-full h-full border-2 border-gilt-brass rounded-full border-t-transparent animate-spin"/>
                                  </div>
                                </div>
                                <div className="flex flex-col">
                                  <span className="text-sm font-semibold text-gilt-ink">Processing Request</span>
                                  <span className="text-xs text-gilt-soft">Analyzing your query</span>
                                </div>
                              </div>
                              <div className="ml-auto">
                                <span className={`material-icons text-gilt-faint transition-transform duration-300 ${message.isThinkingVisible ? 'rotate-180' : ''}`}>
                                  expand_more
                                </span>
                              </div>
                            </div>

                            {message.isThinkingVisible && (
                              <div className="p-5 bg-gilt-card border-t border-gilt-hairline">
                                <div className="flex items-start space-x-4 animate-fadeIn">
                                  <div className="relative mt-1">
                                    <div className="absolute inset-0 bg-gilt-brass rounded-full animate-ping opacity-30"/>
                                    <div className="relative w-2.5 h-2.5 rounded-full bg-gilt-brass"/>
                                  </div>
                                  <div className="flex-1">
                                    <div className="text-sm font-medium text-gilt-ink leading-relaxed">
                                      {message.thinking[message.thinking.length - 1]?.content}
                                    </div>
                                    <div className="mt-2 flex items-center space-x-2">
                                      <div className="flex items-center space-x-1">
                                        <span className="w-1.5 h-1.5 rounded-full bg-gilt-faint animate-pulse"/>
                                        <span className="w-1.5 h-1.5 rounded-full bg-gilt-faint animate-pulse" style={{ animationDelay: '0.2s' }}/>
                                        <span className="w-1.5 h-1.5 rounded-full bg-gilt-faint animate-pulse" style={{ animationDelay: '0.4s' }}/>
                                      </div>
                                      <span className="font-mono tabular-nums text-xs text-gilt-faint">
                                        {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                      </span>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      <div className="w-full flex items-start space-x-4 px-1">
                        <div className="flex-shrink-0 mt-1">
                          {message.sender === "bot" && (
                            <img
                              src={logo}
                              className="w-7 h-7 transition-opacity duration-300 opacity-100"
                              style={{ filter: 'grayscale(100%)' }}
                              alt="Bot Logo"
                            />
                          )}
                        </div>
                        <div className="flex-grow prose max-w-none">
                          {isTyping && !message.thinking && !message.text ? (
                            <div className="flex items-center space-x-1">
                              <div className="w-2 h-2 bg-gilt-faint rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                              <div className="w-2 h-2 bg-gilt-faint rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                              <div className="w-2 h-2 bg-gilt-faint rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                            </div>
                          ) : (
                            <RenderMessageContent message={message.text} />
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div ref={chatEndRef}/>
        <div
            className={`my-5 p-4 bg-gilt-card border border-gilt-hairline focus-within:border-gilt-brass transition-colors duration-150 left-0 md:left-auto right-0 md:right-auto w-full max-w-sm md:max-w-4xl mx-auto rounded-xl flex flex-col space-y-3 ${
                messages.length > 0 ? "fixed bottom-16 md:bottom-0" : ""
            }`}
        >
          {(!client && !vault) && (
            <div className="flex items-center space-x-2 mb-2">
              <div className="relative" ref={menuRef}>
                <button
                  onClick={() => setShowChatbotMenu(!showChatbotMenu)}
                  className="flex items-center justify-between px-3 py-2 rounded-lg bg-gilt-sunken border border-gilt-hairline hover:border-gilt-brass transition-colors duration-150 min-w-[200px] group"
                >
                  <span className="flex items-center space-x-2">
                    {selectedChatbot ? (
                      <>
                        <span className="text-gilt-brass">{selectedChatbot.icon}</span>
                        <span className="text-sm font-medium text-gilt-ink">{selectedChatbot.name}</span>
                      </>
                    ) : (
                      <>
                        <LucideFileText size={20} className="text-gilt-faint" />
                        <span className="text-sm font-medium text-gilt-soft">Select Advisor</span>
                      </>
                    )}
                  </span>
                  <div className="flex items-center space-x-1">
                    {selectedChatbot && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedChatbot(null);
                        }}
                        className="p-1 text-gilt-faint hover:text-gilt-ink hover:bg-gilt-card rounded-full transition-colors duration-150 opacity-0 group-hover:opacity-100"
                      >
                        <span className="material-icons text-sm">close</span>
                      </button>
                    )}
                    <span className="material-icons text-sm text-gilt-faint">expand_more</span>
                  </div>
                </button>
                
                {showChatbotMenu && (
                  <div className={`absolute ${
                    dropdownPosition === 'bottom' ? 'top-full mt-2' : 'bottom-full mb-2'
                  } w-[280px] bg-gilt-card rounded-xl border border-gilt-hairline overflow-hidden z-50 max-h-[400px] flex flex-col`}>
                    <div className="p-3 border-b border-gilt-hairline bg-gilt-card sticky top-0 z-10">
                      <h3 className="text-sm font-semibold text-gilt-ink">Select an Advisor</h3>
                      <p className="text-xs text-gilt-soft mt-1">Choose the expert best suited for your query</p>
                    </div>
                    <div className="overflow-y-auto">
                      {filteredChatbots.map((bot) => (
                        <button
                          key={bot.id}
                          onClick={() => handleChatbotSelect(bot)}
                          className={`w-full px-4 py-3 flex items-start space-x-3 hover:bg-gilt-sunken transition-colors duration-150 relative group ${
                            selectedChatbot?.id === bot.id ? 'bg-gilt-sunken' : ''
                          }`}
                        >
                          <span className={`mt-1 ${selectedChatbot?.id === bot.id ? 'text-gilt-brass' : 'text-gilt-faint'}`}>
                            {bot.icon}
                          </span>
                          <div className="text-left flex-1 min-w-0">
                            <div className={`font-medium ${selectedChatbot?.id === bot.id ? 'text-gilt-brass' : 'text-gilt-ink'}`}>
                              {bot.name}
                            </div>
                            <div className="text-sm text-gilt-soft">
                              {bot.description}
                            </div>
                            <div className="font-mono text-xs text-gilt-faint mt-1">
                              {bot.trigger}
                            </div>
                          </div>
                          {selectedChatbot?.id === bot.id && (
                            <span className="absolute right-4 top-4 text-gilt-brass material-icons text-lg">
                              check_circle
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                    <div className="px-4 py-2.5 bg-gilt-sunken border-t border-gilt-hairline sticky bottom-0">
                      <div className="flex items-center text-xs text-gilt-faint">
                        <span className="material-icons text-sm mr-1">info</span>
                        <span>You can also use @ mentions to quickly switch advisors</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>)}

            <div className="flex items-center justify-between space-x-4">
              <TextareaAutosize
                minRows={1}
                maxRows={6}
                value={input}
                onChange={handleInputChange}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !isTyping) {
                    e.preventDefault();
                    sendMessage(input);
                  }
                }}
                placeholder="Ask anything..."
                className="flex-1 px-4 py-2 rounded-md outline-none bg-transparent text-gilt-ink placeholder:text-gilt-faint text-[14px] md:text-[16px] resize-none"
              />
              
              <div className="flex items-center space-x-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  onChange={handleFileUpload}
                  className="hidden"
                  accept="image/*,application/pdf,.doc,.docx,.txt"
                />
                <button
                  onClick={handleAddAttachmentClick}
                  className="p-2 text-gilt-faint hover:text-gilt-ink rounded-full hover:bg-gilt-sunken transition-colors duration-150"
                >
                  <span className="material-icons">attach_file</span>
                </button>
                
                <button
                  onClick={() => sendMessage(input)}
                  disabled={isTyping}
                  className={`p-2 bg-gilt-ink ${
                    isTyping ? "opacity-40" : "hover:opacity-85"
                  } text-gilt-paper rounded-md w-10 h-10 flex items-center justify-center transition-opacity duration-150`}
                >
                  <span className="material-icons">arrow_forward</span>
                </button>
              </div>
            </div>

            {uploadedFile && (
              <div
                key={uploadedFile.name}
                className="relative w-48 p-3 border border-gilt-hairline rounded-lg bg-gilt-card hover:bg-gilt-sunken transition-colors duration-150"
              >
                {uploadedFile.type.startsWith("image/") ? (
                  <>
                    <div className="relative h-32 mb-2">
                      <img
                        src={URL.createObjectURL(uploadedFile)}
                        alt="Uploaded preview"
                        className="w-full h-full object-cover rounded-md"
                      />
                    </div>
                    <div className="flex items-center space-x-2">
                      <span className={`material-icons ${getFileColor(uploadedFile.type)}`}>
                        {getFileIcon(uploadedFile.type)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gilt-ink truncate">
                          {uploadedFile.name}
                        </p>
                        <p className="font-mono tabular-nums text-xs text-gilt-faint">
                          {(uploadedFile.size / 1024).toFixed(1)} KB
                        </p>
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="flex items-start space-x-3">
                    <span className={`material-icons text-3xl ${getFileColor(uploadedFile.type)}`}>
                      {getFileIcon(uploadedFile.type)}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gilt-ink truncate">
                        {uploadedFile.name}
                      </p>
                      <p className="font-mono tabular-nums text-xs text-gilt-faint flex items-center space-x-1">
                        <span>{(uploadedFile.size / 1024).toFixed(1)} KB</span>
                        <span>•</span>
                        <span className="uppercase">{uploadedFile.type.split('/')[1]}</span>
                      </p>
                    </div>
                  </div>
                )}
                <button
                  onClick={() => setUploadedFile(null)}
                  className="absolute -top-2 -right-2 w-6 h-6 bg-gilt-card rounded-full flex items-center justify-center border border-gilt-hairline text-gilt-faint hover:text-gilt-ink transition-colors duration-150"
                  title="Remove"
                >
                  <span className="material-icons text-sm">close</span>
                </button>
              </div>
            )}
        </div>
      </div>
    </div>
  );
};

export default ChatbotLight;
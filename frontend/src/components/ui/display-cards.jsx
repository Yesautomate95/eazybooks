"use client";

import { cn } from "../../lib/utils";
import { Sparkles } from "lucide-react";

const scrollToFeature = (featureId) => {
  const element = document.getElementById(featureId);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth' });
  }
};

function DisplayCard({
  className,
  icon = <Sparkles className="size-4 text-blue-300" />,
  title = "Featured",
  description = "Discover amazing content",
  date = "Just now",
  iconClassName = "text-blue-500",
  titleClassName = "text-blue-500",
  featureId,
}) {
  return (
    <div
      onClick={() => featureId && scrollToFeature(featureId)}
      className={cn(
        "relative flex h-36 w-[22rem] -skew-y-[8deg] select-none flex-col justify-between rounded-xl border-2 border-white/20 bg-[#030303] px-4 py-3 transition-all duration-700 hover:border-white/40 hover:shadow-[0_0_30px_rgba(8,148,255,0.3)] hover:scale-105 [&>*]:flex [&>*]:items-center [&>*]:gap-2 cursor-pointer shadow-[0_0_15px_rgba(255,255,255,0.1)]",
        className
      )}
    >
      <div>
        <span className="relative inline-block rounded-full bg-gradient-to-r from-[#0894FF] to-[#C959DD] p-1.5 shadow-[0_0_10px_rgba(8,148,255,0.3)]">
          {icon}
        </span>
        <p className={cn("text-lg font-medium bg-clip-text text-transparent bg-gradient-to-r from-[#0894FF] to-[#C959DD]", titleClassName)}>{title}</p>
      </div>
      <p className="text-white/80">{description}</p>
      <p className="text-white/60">{date}</p>
    </div>
  );
}

function DisplayCards({ cards }) {
  const defaultCards = [
    {
      icon: <Sparkles className="size-4 text-white" />,
      title: "Data Chat",
      description: "Natural Language Queries",
      date: "Instant Insights",
      featureId: "feature-queries",
      className: "[grid-area:stack] hover:-translate-y-10 hover:shadow-[0_0_40px_rgba(8,148,255,0.4)] hover:border-[#0894FF]/30 hover:shadow-[0_0_20px_rgba(255,255,255,0.2)]",
    },
    {
      icon: <Sparkles className="size-4 text-white" />,
      title: "Auto Entry",
      description: "Automated Data Entry",
      date: "Save Hours of Work",
      featureId: "feature-data-entry",
      className: "[grid-area:stack] translate-x-16 translate-y-10 hover:-translate-y-1 hover:shadow-[0_0_40px_rgba(201,89,221,0.4)] hover:border-[#C959DD]/30 hover:shadow-[0_0_20px_rgba(255,255,255,0.2)]",
    },
    {
      icon: <Sparkles className="size-4 text-white" />,
      title: "Document AI",
      description: "Smart Document Management",
      date: "Intelligent Insights",
      featureId: "feature-locker",
      className: "[grid-area:stack] translate-x-32 translate-y-20 hover:translate-y-10 hover:shadow-[0_0_40px_rgba(255,46,84,0.4)] hover:border-[#FF2E54]/30 hover:shadow-[0_0_20px_rgba(255,255,255,0.2)]",
    },
  ];

  const displayCards = cards || defaultCards;

  return (
    <div className="grid [grid-template-areas:'stack'] place-items-center opacity-100 animate-in fade-in-0 duration-700">
      {displayCards.map((cardProps, index) => (
        <DisplayCard key={index} {...cardProps} />
      ))}
    </div>
  );
}

export default DisplayCards; 
import CreateEntity from "./CreateEntity"; // adjust the path as needed

export default function CreateLocker({ onBack }) {
  return (
    <CreateEntity
      onBack={onBack}
      entityLabel="Client"
      createEndpoint={`${process.env.REACT_APP_API_URL}/ca/create-client`}
      entityIdKey="client_id"
      description="Add a new client to manage their files and chats"
    />
  );
}

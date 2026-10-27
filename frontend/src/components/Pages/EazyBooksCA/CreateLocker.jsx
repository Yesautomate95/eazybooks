import CreateEntity from "./CreateEntity"; // adjust the path as needed

export default function CreateLocker({ onBack }) {
  return (
    <CreateEntity
      onBack={onBack}
      entityLabel="Locker"
      createEndpoint={`${process.env.REACT_APP_API_URL}/ca/create-locker`}
      entityIdKey="lockr_id"
      description="Organize your files with a new locker"
    />
  );
}

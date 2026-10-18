import { findFleetRun } from '../useFleetRuns';
import { getFleetRun } from '@/lib/api/fleet';
import { APIError } from '@/lib/api';

jest.mock('@/lib/api/fleet', () => ({ getFleetRun: jest.fn(), getFleetRuns: jest.fn() }));
const mockGet = getFleetRun as jest.Mock;

describe('findFleetRun', () => {
  beforeEach(() => jest.clearAllMocks());

  it('reads the single-run route', async () => {
    mockGet.mockResolvedValue({ id: 'job-1', conversation_id: 'conv-1', engine: true });
    await expect(findFleetRun('job-1')).resolves.toMatchObject({ id: 'job-1', conversation_id: 'conv-1' });
    expect(mockGet).toHaveBeenCalledWith('job-1');
  });

  it("a 404 is null (gone, or someone else's); other errors surface", async () => {
    mockGet.mockRejectedValueOnce(new APIError('nope', 404));
    await expect(findFleetRun('job-2')).resolves.toBeNull();
    mockGet.mockRejectedValueOnce(new APIError('boom', 500));
    await expect(findFleetRun('job-3')).rejects.toMatchObject({ status: 500 });
  });
});

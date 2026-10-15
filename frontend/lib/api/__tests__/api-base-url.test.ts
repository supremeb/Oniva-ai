describe('API_BASE_URL', () => {
  const original = process.env.NEXT_PUBLIC_API_URL;
  afterEach(() => {
    if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = original;
    jest.resetModules();
  });

  it('reads NEXT_PUBLIC_API_URL', () => {
    process.env.NEXT_PUBLIC_API_URL = 'https://api.example.test';
    jest.resetModules();
    expect(require('@/lib/api/index').API_BASE_URL).toBe('https://api.example.test');
  });

  it('falls back to localhost', () => {
    delete process.env.NEXT_PUBLIC_API_URL;
    jest.resetModules();
    expect(require('@/lib/api/index').API_BASE_URL).toBe('http://localhost:8000');
  });
});

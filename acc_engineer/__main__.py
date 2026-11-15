def main():
    import uvicorn
    uvicorn.run("acc_engineer.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
